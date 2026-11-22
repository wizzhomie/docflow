"""QLoRA fine-tuning for the Docflow meaning-layer pointer task.

Train on chat JSONL produced by `finetune/corpus/make_training_data.py`.
Loss is computed on the assistant JSON only: the long Ukrainian prompt is masked
with -100, so the model learns where to point, not to reproduce the prompt.

    pip install torch transformers peft trl bitsandbytes datasets accelerate
    python finetune/train_qlora.py --epochs 1
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import torch
from datasets import Dataset
from peft import LoraConfig, PeftModel, get_peft_model, prepare_model_for_kbit_training
from transformers import (
    AutoModelForCausalLM,
    AutoTokenizer,
    BitsAndBytesConfig,
    DataCollatorForSeq2Seq,
    Trainer,
    TrainingArguments,
)

FT = Path(__file__).resolve().parent
BASE = FT.parent
TRAIN_DIR = FT / 'data' / 'training'


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding='utf-8-sig') as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def build_dataset(path: Path, tokenizer, max_len: int) -> Dataset:
    records = read_jsonl(path)
    features = []
    dropped = 0
    for record in records:
        messages = record['messages']
        answer = messages[-1]['content']
        prompt_messages = messages[:-1]
        prompt = tokenizer.apply_chat_template(
            prompt_messages, tokenize=False, add_generation_prompt=True,
            enable_thinking=False,
        )
        prompt_ids = tokenizer(prompt, add_special_tokens=False)['input_ids']
        answer_ids = tokenizer(answer + tokenizer.eos_token, add_special_tokens=False)['input_ids']
        # A record that does not fit is dropped, not truncated: half an answer teaches
        # the model to stop in the middle of the JSON, which is worse than not seeing it.
        if len(prompt_ids) + len(answer_ids) > max_len:
            dropped += 1
            continue
        input_ids = prompt_ids + answer_ids
        labels = [-100] * len(prompt_ids) + answer_ids
        if len(input_ids) < 8 or all(label == -100 for label in labels):
            continue
        features.append({
            'input_ids': input_ids,
            'attention_mask': [1] * len(input_ids),
            'labels': labels,
        })
    if dropped:
        print(f'dropped {dropped} records longer than {max_len} tokens', flush=True)
    return Dataset.from_list(features)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', default='Qwen/Qwen3-1.7B')
    parser.add_argument('--adapter', type=Path, default=None,
                        help='existing PEFT adapter to continue training from')
    parser.add_argument('--train', type=Path, default=TRAIN_DIR / 'train.jsonl')
    parser.add_argument('--val', type=Path, default=TRAIN_DIR / 'val.jsonl')
    parser.add_argument('--out', type=Path, default=FT / 'data' / 'adapter-qwen3-1.7b')
    parser.add_argument('--epochs', type=float, default=3.0)
    parser.add_argument('--seq-len', type=int, default=1024)
    parser.add_argument('--lr', type=float, default=1e-4)
    parser.add_argument('--grad-accum', type=int, default=8)
    parser.add_argument('--max-steps', type=int, default=-1)
    args = parser.parse_args()

    os.environ.setdefault('PYTORCH_CUDA_ALLOC_CONF', 'expandable_segments:True')
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.backends.cudnn.allow_tf32 = False

    tokenizer = AutoTokenizer.from_pretrained(args.model, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    tokenizer.padding_side = 'right'

    quant = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type='nf4',
        bnb_4bit_use_double_quant=True,
        bnb_4bit_compute_dtype=torch.float16,
        bnb_4bit_quant_storage=torch.uint8,
    )
    model = AutoModelForCausalLM.from_pretrained(
        args.model,
        quantization_config=quant,
        device_map={'': 0},
        torch_dtype=torch.float16,
        trust_remote_code=True,
        attn_implementation='sdpa',
    )
    model.config.use_cache = False
    model = prepare_model_for_kbit_training(model, use_gradient_checkpointing=True)
    if args.adapter is not None:
        model = PeftModel.from_pretrained(model, str(args.adapter), is_trainable=True)
    else:
        lora = LoraConfig(
            r=16,
            lora_alpha=32,
            lora_dropout=0.05,
            bias='none',
            task_type='CAUSAL_LM',
            target_modules=['q_proj', 'k_proj', 'v_proj', 'o_proj',
                            'gate_proj', 'up_proj', 'down_proj'],
        )
        model = get_peft_model(model, lora)
    model.enable_input_require_grads()
    model.print_trainable_parameters()

    train_dataset = build_dataset(args.train, tokenizer, args.seq_len)
    val_dataset = build_dataset(args.val, tokenizer, args.seq_len)
    print(f'train examples: {len(train_dataset)}, val examples: {len(val_dataset)}', flush=True)
    if not len(train_dataset):
        raise SystemExit('no training examples after tokenization')

    collator = DataCollatorForSeq2Seq(
        tokenizer, model=model, padding=True, pad_to_multiple_of=8, label_pad_token_id=-100,
    )

    training_args = TrainingArguments(
        output_dir=str(args.out),
        per_device_train_batch_size=1,
        per_device_eval_batch_size=1,
        gradient_accumulation_steps=args.grad_accum,
        num_train_epochs=args.epochs,
        max_steps=args.max_steps,
        learning_rate=args.lr,
        lr_scheduler_type='cosine',
        warmup_steps=3,
        weight_decay=0.0,
        optim='paged_adamw_8bit',
        max_grad_norm=1.0,
        fp16=True,
        bf16=False,
        gradient_checkpointing=True,
        gradient_checkpointing_kwargs={'use_reentrant': False},
        logging_steps=5,
        save_strategy='epoch',
        save_total_limit=2,
        eval_strategy='no',
        report_to=[],
        remove_unused_columns=False,
        dataloader_num_workers=0,
        seed=42,
        data_seed=42,
    )

    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=train_dataset,
        eval_dataset=val_dataset,
        data_collator=collator,
    )
    trainer.train()
    args.out.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(args.out)
    tokenizer.save_pretrained(args.out)
    peak = torch.cuda.max_memory_allocated() / 1024 ** 3 if torch.cuda.is_available() else 0
    (args.out / 'training-summary.json').write_text(json.dumps({
        'base_model': args.model,
        'train_examples': len(train_dataset),
        'val_examples': len(val_dataset),
        'epochs': args.epochs,
        'seq_len': args.seq_len,
        'lr': args.lr,
        'grad_accum': args.grad_accum,
        'peak_vram_gb': round(peak, 2),
    }, indent=1) + '\n', encoding='utf-8')
    print(f'saved adapter: {args.out}; peak VRAM: {peak:.2f} GB', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
