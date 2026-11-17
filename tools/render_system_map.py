"""Render the one-page system map for the plain-language guide.

Run: python3 tools/render_system_map.py
Output: docs/system-map.svg

Plain SVG, no external fonts or scripts. The map answers one question: what
happens to a document between arriving and becoming a CRM deal, and who owns
each step (system, human, external CRM).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from render_process_maps import CRM, DOCS, HUMAN, INK, MUTED, RISK, SYSTEM, arrow, box, defs, header, svg

WIDTH = 1240


def render():
    parts = [defs()]

    # Row 1: intake -> recognition -> formal control -> queue
    parts.append(box(40, 120, 260, 92, 'Intake', ['folder data/inbox', 'file upload', 'a sample document'], SYSTEM))
    parts.append(box(340, 120, 290, 92, 'Recognition', ['text or OCR for scans', 'document type detection', 'fields + confidence score'], SYSTEM))
    parts.append(box(670, 120, 240, 92, 'Formal validation', ['amounts, dates, tax ID', 'e-mail, phone, IBAN', 'reports signals, never fixes'], SYSTEM))
    parts.append(box(950, 120, 250, 92, 'Review queue', ['the document never vanishes', 'signal count is visible', 'opens in one click'], SYSTEM))

    parts.append(arrow([(300, 166), (338, 166)]))
    parts.append(arrow([(630, 166), (668, 166)]))
    parts.append(arrow([(910, 166), (948, 166)]))

    # Profiles feeding recognition
    parts.append(box(340, 30, 290, 56, 'Document-type profile', ['invoice / act: own hints and fields'], MUTED, dashed=True))
    parts.append(arrow([(485, 86), (485, 118)], dashed=True))

    # Row 2: human review -> CRM -> history
    parts.append(box(100, 300, 280, 100, 'Human in review', ['checks and edits fields', 'presses Confirm', 'sees the result card'], HUMAN))
    parts.append(box(470, 300, 300, 100, 'CRM connector', ['Uspacy or Bitrix24', 'company + bank details', 'contact and deal'], CRM))
    parts.append(box(860, 300, 250, 100, 'History and metrics', ['what was confirmed', 'how much time was saved', 'what gets corrected most'], SYSTEM))

    parts.append(arrow([(1075, 212), (1075, 260), (240, 260), (240, 298)]))
    parts.append(arrow([(380, 350), (468, 350)]))
    parts.append(arrow([(770, 350), (858, 350)]))

    # Duplicate protection across both stores
    parts.append(box(470, 440, 300, 76, 'Duplicate protection', ['local history', 'a check inside the CRM itself'], RISK, dashed=True))
    parts.append(arrow([(620, 438), (620, 402)], dashed=True))

    parts.append(box(40, 440, 380, 76, 'What the system does', ['the system reads and checks formally', 'the human decides and owns the result'], MUTED, dashed=True))

    parts.append(
        f'<text x="40" y="560" font-size="13" fill="{MUTED}">'
        'The map answers one question: what happens to a document between intake and a deal in the CRM.'
        '</text>'
        f'<text x="40" y="582" font-size="13" fill="{MUTED}">'
        'Blue blocks are system steps, orange is a human step, green is the external CRM, red is a safeguard.'
        '</text>'
    )
    return svg(WIDTH, 620, ''.join(parts), 'DocFlow: the path of a document')


def main() -> int:
    target = DOCS / 'system-map.svg'
    target.write_text(render(), encoding='utf-8')
    print('written:', target)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
