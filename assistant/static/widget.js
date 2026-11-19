/* DocFlow assistant widget.
 *
 * Drop it into any DocFlow page with:
 *   <script src="https://<operator-host>/widget.js" defer></script>
 *
 * It renders a floating button and a chat panel, and talks to the operator API on the
 * same origin it was loaded from. All styles are scoped to the dfo- prefix so the host
 * page cannot be affected.
 */
(function () {
  'use strict';
  if (window.__docflowAssistantLoaded) return;
  window.__docflowAssistantLoaded = true;

  var script = document.currentScript;
  var API = script ? new URL(script.src).origin : window.location.origin;
  // The product demo, when this widget is served from its own host (chat.*).
  var demoUrl = '';
  fetch(API + '/api/config').then(function (r) { return r.json(); }).then(function (config) {
    demoUrl = (config && config.demo_url) || '';
  }).catch(function () {});

  var EXAMPLES = [
    'What is waiting for review today?',
    'Which documents need approval?',
    'How do I confirm a document?',
    'Why did this invoice go to approval?',
    'Is there a duplicate in the queue?',
  ];

  var messages = [];
  var busy = false;

  var style = document.createElement('style');
  style.textContent = [
    '.dfo-bubble{position:fixed;right:22px;bottom:22px;width:60px;height:60px;border-radius:50%;padding:0;',
    'border:0;cursor:pointer;z-index:2147483000;background:conic-gradient(from 180deg,#7c5cff,#22d3ee,#ff5c9d,#7c5cff);',
    'box-shadow:0 12px 34px -8px rgba(124,92,255,.7),0 0 0 1px rgba(255,255,255,.06);',
    'transition:transform .15s ease,box-shadow .2s ease;}',
    '.dfo-bubble:hover{transform:scale(1.06);box-shadow:0 16px 44px -8px rgba(124,92,255,.85),0 0 0 1px rgba(255,255,255,.1);}',
    '.dfo-core{position:absolute;inset:4px;border-radius:50%;background:#0e1018;display:flex;align-items:center;justify-content:center;}',
    '.dfo-core svg{width:24px;height:24px;stroke:#eceef5;fill:none;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round;}',
    '.dfo-panel{position:fixed;right:22px;bottom:96px;width:392px;max-width:calc(100vw - 28px);height:566px;',
    'max-height:calc(100vh - 132px);background:#0e1018;border:1px solid rgba(255,255,255,.09);border-radius:18px;',
    'box-shadow:0 40px 100px -30px #000,0 0 0 1px rgba(255,255,255,.03) inset;display:flex;flex-direction:column;',
    'overflow:hidden;z-index:2147483001;color:#eceef5;',
    'font:15px/1.55 "Inter",-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;}',
    '.dfo-head{display:flex;align-items:center;gap:12px;padding:15px 17px;border-bottom:1px solid rgba(255,255,255,.09);',
    'background:linear-gradient(180deg,#12141f,#0e1018);}',
    '.dfo-mark{width:28px;height:28px;border-radius:8px;flex:none;position:relative;',
    'background:conic-gradient(from 180deg,#7c5cff,#22d3ee,#ff5c9d,#7c5cff);box-shadow:0 0 20px rgba(124,92,255,.5);}',
    '.dfo-mark::after{content:"";position:absolute;inset:6px;border-radius:50%;background:#0e1018;border:2px solid rgba(255,255,255,.7);}',
    '.dfo-title{font-weight:650;font-size:15px;margin:0;letter-spacing:-.01em;}',
    '.dfo-sub{font-size:12px;color:#9aa0b4;margin:2px 0 0;}',
    '.dfo-close{margin-left:auto;background:none;border:0;color:#9aa0b4;font-size:18px;line-height:1;cursor:pointer;',
    'border-radius:8px;padding:5px 8px;transition:background .15s,color .15s;}',
    '.dfo-close:hover{background:rgba(255,255,255,.06);color:#eceef5;}',
    '.dfo-log{flex:1;overflow-y:auto;padding:16px;display:flex;flex-direction:column;gap:12px;background:#0b0d14;scrollbar-width:thin;}',
    '.dfo-log::-webkit-scrollbar{width:8px;}.dfo-log::-webkit-scrollbar-thumb{background:rgba(255,255,255,.12);border-radius:4px;}',
    '.dfo-msg{max-width:88%;padding:10px 13px;border-radius:14px;white-space:pre-wrap;word-break:break-word;font-size:14.5px;}',
    '.dfo-msg.user{align-self:flex-end;background:linear-gradient(135deg,#7c5cff,#5b6cff);color:#fff;',
    'border-bottom-right-radius:4px;box-shadow:0 8px 22px -10px rgba(124,92,255,.8);}',
    '.dfo-msg.bot{align-self:flex-start;background:#12141f;border:1px solid rgba(255,255,255,.08);color:#eceef5;',
    'border-bottom-left-radius:4px;}',
    '.dfo-msg.err{align-self:flex-start;background:rgba(248,113,113,.12);border:1px solid rgba(248,113,113,.35);color:#fecaca;}',
    '.dfo-trace{align-self:flex-start;max-width:92%;font-size:12px;color:#9aa0b4;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;}',
    '.dfo-proposal{align-self:flex-start;max-width:92%;border:1px solid rgba(124,92,255,.45);background:rgba(124,92,255,.1);',
    'border-radius:12px;padding:11px 13px;font-size:12.5px;color:#e6e3ff;}',
    '.dfo-proposal b{display:block;font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:#a9a4ff;margin-bottom:6px;}',
    '.dfo-proposal p{margin:0 0 9px;line-height:1.45;}',
    '.dfo-proposal a{display:inline-block;background:#7454ff;color:#fff;text-decoration:none;font-weight:600;',
    'padding:7px 12px;border-radius:9px;font-size:12px;}',
    '.dfo-proposal a:hover{background:#6344ee;}',
    '.dfo-proposal small{display:block;margin-top:8px;color:#9aa0b4;font-size:11px;}',
    '.dfo-trace summary{cursor:pointer;}.dfo-trace summary:hover{color:#eceef5;}',
    '.dfo-trace pre{margin:7px 0 0;white-space:pre-wrap;font:11.5px/1.45 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;',
    'background:#0a0c12;border:1px solid rgba(255,255,255,.07);border-radius:10px;padding:9px;max-height:150px;overflow:auto;color:#b4b9cb;}',
    '.dfo-examples{display:flex;flex-wrap:wrap;gap:6px;padding:2px 16px 14px;background:#0b0d14;}',
    '.dfo-examples button{font:12.5px/1.3 inherit;color:#c9c0ff;background:rgba(124,92,255,.14);',
    'border:1px solid rgba(124,92,255,.28);border-radius:999px;padding:7px 11px;cursor:pointer;text-align:left;',
    'transition:background .15s,border-color .15s;}',
    '.dfo-examples button:hover{background:rgba(124,92,255,.24);border-color:rgba(124,92,255,.5);}',
    '.dfo-form{display:flex;gap:8px;padding:12px 14px;border-top:1px solid rgba(255,255,255,.09);background:#0e1018;}',
    '.dfo-input{flex:1;border:1px solid rgba(255,255,255,.1);border-radius:12px;padding:10px 12px;font:14px/1.4 inherit;',
    'color:#eceef5;background:#12141f;outline:none;min-width:0;}',
    '.dfo-input::placeholder{color:#6b7188;}',
    '.dfo-input:focus{border-color:rgba(124,92,255,.75);box-shadow:0 0 0 3px rgba(124,92,255,.18);}',
    '.dfo-send{border:0;border-radius:12px;background:linear-gradient(135deg,#7c5cff,#22d3ee);color:#fff;',
    'font:600 13.5px/1 inherit;padding:0 16px;cursor:pointer;box-shadow:0 8px 20px -10px rgba(124,92,255,.8);}',
    '.dfo-send[disabled]{opacity:.5;cursor:progress;}',
    '.dfo-dots span{display:inline-block;width:6px;height:6px;margin-right:3px;border-radius:50%;background:#7c5cff;animation:dfo-bounce 1s infinite;}',
    '.dfo-dots span:nth-child(2){animation-delay:.15s}.dfo-dots span:nth-child(3){animation-delay:.3s}',
    '@keyframes dfo-bounce{0%,80%,100%{transform:translateY(0);opacity:.45}40%{transform:translateY(-4px);opacity:1}}',
    '.dfo-hidden{display:none !important;}',
  ].join('');

  var bubble = document.createElement('button');
  bubble.className = 'dfo-bubble';
  bubble.type = 'button';
  bubble.setAttribute('aria-label', 'Open the DocFlow assistant');
  bubble.innerHTML = '<span class="dfo-core"><svg viewBox="0 0 24 24" aria-hidden="true">' +
    '<path d="M4 5.5h16v11H9l-4 3.5v-3.5H4z"/><path d="M8.5 10.5h.01M12 10.5h.01M15.5 10.5h.01"/>' +
    '</svg></span>';

  var panel = document.createElement('div');
  panel.className = 'dfo-panel dfo-hidden';
  panel.innerHTML = [
    '<div class="dfo-head">',
    '  <span class="dfo-mark" aria-hidden="true"></span>',
    '  <div><p class="dfo-title">DocFlow assistant</p>',
    '  <p class="dfo-sub">Platform help and the review queue</p></div>',
    '  <button class="dfo-close" type="button" aria-label="Close">&times;</button>',
    '</div>',
    '<div class="dfo-log"></div>',
    '<div class="dfo-examples"></div>',
    '<form class="dfo-form">',
    '  <input class="dfo-input" type="text" placeholder="Ask about a document or the platform…" autocomplete="off">',
    '  <button class="dfo-send" type="submit">Send</button>',
    '</form>',
  ].join('');

  function el(html) {
    var wrapper = document.createElement('div');
    wrapper.innerHTML = html.trim();
    return wrapper.firstChild;
  }

  function logEl() { return panel.querySelector('.dfo-log'); }
  function exampleBox() { return panel.querySelector('.dfo-examples'); }

  function scrollDown() { logEl().scrollTop = logEl().scrollHeight; }

  function addMessage(role, text) {
    var node = document.createElement('div');
    node.className = 'dfo-msg ' + role;
    node.textContent = text;
    logEl().appendChild(node);
    scrollDown();
    return node;
  }

  function addTrace(steps) {
    if (!steps || !steps.length) return;
    var visible = steps.filter(function (step) { return step.tool !== 'finish'; });
    if (!visible.length) return;
    var details = document.createElement('details');
    details.className = 'dfo-trace';
    var summary = document.createElement('summary');
    summary.textContent = 'How I know: ' + visible.map(function (step) { return step.tool; }).join(', ');
    var pre = document.createElement('pre');
    pre.textContent = visible.map(function (step) {
      return (step.ok ? '✓ ' : '✗ ') + step.tool + ' ' + JSON.stringify(step.arguments || {});
    }).join('\n');
    details.appendChild(summary);
    details.appendChild(pre);
    logEl().appendChild(details);
    scrollDown();
  }

  function addProposal(steps) {
    var step = null;
    (steps || []).forEach(function (item) {
      if (!step && item.tool === 'propose_confirm' && item.result) step = item;
    });
    if (!step) return;
    var result = step.result || {};
    var id = result.doc_id || (step.arguments || {}).doc_id;
    var card = document.createElement('div');
    card.className = 'dfo-proposal';
    var title = document.createElement('b');
    title.textContent = 'Human confirms in DocFlow';
    var text = document.createElement('p');
    text.textContent = result.proposal || 'A person reviews this document and confirms it.';
    card.appendChild(title);
    card.appendChild(text);
    if (demoUrl && id) {
      var link = document.createElement('a');
      link.href = demoUrl.replace(/\/$/, '') + '/app?doc=' + encodeURIComponent(id);
      link.target = '_blank';
      link.rel = 'noopener';
      link.textContent = 'Open in DocFlow';
      card.appendChild(link);
    }
    var note = document.createElement('small');
    note.textContent = 'The assistant never confirms, pays or sends.';
    card.appendChild(note);
    logEl().appendChild(card);
    scrollDown();
  }

  function rememberRun(question, data) {
    try {
      var runs = JSON.parse(window.localStorage.getItem('dfo-runs') || '[]');
      runs.unshift({
        q: question,
        tools: (data.steps || []).map(function (step) { return step.tool; })
          .filter(function (tool) { return tool !== 'finish'; }),
        ms: data.total_ms || 0,
        at: Date.now(),
      });
      window.localStorage.setItem('dfo-runs', JSON.stringify(runs.slice(0, 8)));
    } catch (error) {}
  }

  function showTyping() {
    var node = document.createElement('div');
    node.className = 'dfo-msg bot dfo-dots';
    node.innerHTML = '<span></span><span></span><span></span>';
    logEl().appendChild(node);
    scrollDown();
    return node;
  }

  function renderExamples() {
    var box = exampleBox();
    box.innerHTML = '';
    EXAMPLES.forEach(function (text) {
      var button = document.createElement('button');
      button.type = 'button';
      button.textContent = text;
      button.addEventListener('click', function () {
        var input = panel.querySelector('.dfo-input');
        input.value = text;
        input.focus();
      });
      box.appendChild(button);
    });
  }

  function send(text) {
    if (busy || !text.trim()) return;
    busy = true;
    var input = panel.querySelector('.dfo-input');
    var sendButton = panel.querySelector('.dfo-send');
    input.value = '';
    input.disabled = true;
    sendButton.disabled = true;
    exampleBox().classList.add('dfo-hidden');
    messages.push({ role: 'user', content: text });
    addMessage('user', text);
    var typing = showTyping();

    fetch(API + '/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ messages: messages.slice(-8) }),
    })
      .then(function (response) {
        return response.json().then(function (data) { return { ok: response.ok, data: data }; });
      })
      .then(function (result) {
        typing.remove();
        if (!result.ok) throw new Error(result.data.detail || 'request failed');
        var data = result.data;
        var answer = (data.answer || '').trim() || 'I could not find an answer.';
        addMessage('bot', answer);
        addTrace(data.steps);
        addProposal(data.steps);
        rememberRun(text, data);
        messages.push({ role: 'assistant', content: answer });
      })
      .catch(function (error) {
        typing.remove();
        addMessage('err', 'Something went wrong: ' + error.message + '. The assistant could be busy, try again.');
      })
      .then(function () {
        busy = false;
        input.disabled = false;
        sendButton.disabled = false;
        input.focus();
      });
  }

  function openPanel() {
    panel.classList.remove('dfo-hidden');
    if (!logEl().childElementCount) {
      addMessage('bot', 'Hi! I am the DocFlow assistant. I can explain how the platform works and answer questions about the review queue: what is waiting, what needs approval, whether a document is a duplicate.');
      renderExamples();
    }
    panel.querySelector('.dfo-input').focus();
  }

  bubble.addEventListener('click', openPanel);
  panel.querySelector('.dfo-close').addEventListener('click', function () {
    panel.classList.add('dfo-hidden');
  });
  panel.querySelector('.dfo-form').addEventListener('submit', function (event) {
    event.preventDefault();
    send(panel.querySelector('.dfo-input').value);
  });

  function mount() {
    document.head.appendChild(style);
    document.body.appendChild(bubble);
    document.body.appendChild(panel);
    window.DocFlowAssistant = { open: openPanel, ask: function (text) {
      panel.classList.remove('dfo-hidden');
      send(text);
    } };
    if (document.body.dataset.assistantOpen === 'true') openPanel();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', mount);
  } else {
    mount();
  }
})();
