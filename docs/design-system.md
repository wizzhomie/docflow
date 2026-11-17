# DocFlow design system

Version 0.1 · 16.11.2026 · scope: showcase (`/`) and review workspace (`/app`).

This is the single source of truth for color, typography, spacing, shape, motion, grid, and
components. It exists so the two pages stop drifting apart and so every later change has a number
to point to instead of an argument about taste. The rendered reference for all tokens sits next
to it: [`design-system.html`](design-system.html).

## 1. Direction

DocFlow is a deterministic router for business documents: a file arrives, the system reads it,
a human confirms it, and the document goes to the process and CRM it belongs to. The interface
should feel like a precise tool for one job, not a dashboard.

- **The product's signature is the route.** A document's path from intake to destination is the
  interface's own language: the connector, the destination name, the reason. Boldness is spent on
  the router scene on the showcase; the workspace stays quiet and precise.
- **One accent, one meaning.** Purple means action and decision, amber means a human must look,
  cyan means destination and flow, green means confirmed, red means blocked. In the product, color
  is never decoration.
- **Character comes from the data.** Figures are tabular, amounts carry their currency, dates read
  the same everywhere. Typography is neutral so the document's data holds the page.
- **Density is honest.** The reviewer scans fields and queue rows: the interface is dense, but
  never below 12 px or below 44 px for a touch target on mobile.

Deliberately outside the system: a light theme, a second typeface, a decorative icon set, an
LLM-style "assistant" surface. None of it serves the pipeline.

## 2. Color

Dark theme only. The base is almost black so the white paper of the document preview is the
brightest object on screen.

### Surfaces and lines

| Token | Value | Application |
| --- | --- | --- |
| `--color-bg` | `#07080d` | page background |
| `--color-surface` | `#0e1018` | cards, panels |
| `--color-surface-raised` | `#12141f` | input fields, nested panels |
| `--color-surface-hover` | `#181b29` | row and card hover |
| `--color-line` | `rgba(255,255,255,.09)` | base hairline |
| `--color-line-strong` | `rgba(255,255,255,.16)` | accent divider, border with meaning |

### Text

| Token | Value | Contrast on `--color-surface` | Application |
| --- | --- | --- | --- |
| `--color-text` | `#eceef5` | 16.4:1 | headings, values, primary text |
| `--color-text-2` | `#b4b9cb` | 9.7:1 | descriptions, secondary values |
| `--color-text-3` | `#9aa0b4` | 7.3:1 | labels, hints, metadata |
| `--color-text-4` | `#7f8599` | 5.2:1 | empty states, timestamps, the least important text |

`--color-text-4` is the floor: it replaces the old `#5d6377` (3.2:1), which failed WCAG AA at
every size it was used at.

### Accents and semantics

| Token | Value | Meaning in the interface | Application |
| --- | --- | --- | --- |
| `--color-accent` | `#7c5cff` | brand / decision | focus ring, selected borders, mark, links |
| `--color-accent-fill` | `#7454ff` | action fill | primary button background (white label, 4.7:1) |
| `--color-accent-fill-hover` | `#6f4cf5` | hover fill | darkens on hover |
| `--color-accent-fill-press` | `#6445ee` | pressed fill | darkens further |
| `--color-accent-text` | `#b9a7ff` | accent text | links and labels on dark (9.1:1) |
| `--color-accent-soft` | `rgba(124,92,255,.14)` | soft fill | selected row highlight, focus fill |
| `--color-attention` | `#fbbf24` | a human must look | low-confidence field, "needs review", warnings |
| `--color-attention-text` | `#fcd34d` | attention text | warning text on dark (13.2:1) |
| `--color-attention-soft` | `rgba(251,191,36,.14)` | soft attention fill | warning surface |
| `--color-on-attention` | `#221604` | text on amber | amber badges and CTAs on the showcase (10.6:1) |
| `--color-flow` | `#22d3ee` | destination / flow | destination names, route lines (10.5:1) |
| `--color-flow-soft` | `rgba(34,211,238,.12)` | soft route fill | recognized field chips, route glow |
| `--color-success` | `#34d399` | confirmed / safe | saved, high confidence, auto-pass |
| `--color-success-text` | `#6ee7b7` | success text | success text on dark (12.5:1) |
| `--color-success-soft` | `rgba(52,211,153,.12)` | soft success fill | "confirmed" surface |
| `--color-danger` | `#f87171` | blocked / error | CRM error, missing value, destructive action |
| `--color-danger-text` | `#fca5a5` | error text | error text on dark (10.0:1) |
| `--color-danger-soft` | `rgba(248,113,113,.12)` | soft error fill | error surface |
| `--color-on-accent` | `#ffffff` | text on purple | primary button label |

Rules:

1. **One accent per meaning.** Decision — purple. A human must look — amber. Amber never means
   "action" in the product: the review screen already reads amber as "check this".
2. **Amber is not a CTA color.** The showcase keeps the hero's warm glow as atmosphere, but the
   CTA uses the same purple action fill as the product. Decision D1 was made on 16.11: there is no
   amber button in the system.
3. **Never color alone.** Confidence is a dot plus a percentage plus a border; a route is a name
   plus an arrow, not just a blue word.
4. **Semantic colors are reserved**: the workspace has no decorative green, amber, or cyan glows.
5. All text passes 4.5:1. Meaning-bearing non-color indicators — focus ring, status dots,
   confidence and error borders — pass 3:1 against the surface they sit on. Thin divider lines are
   decorative and never encode state on their own.

## 3. Typography

One family: **Inter** for the interface and showcase copy, with a neutral fallback stack.
Character comes from the route language and color, not a second typeface: a second family would
only compete with the document's data (decision D2: ship Inter as woff2, Latin + Cyrillic).

```css
--font-sans: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
             "Helvetica Neue", Arial, sans-serif;
--font-mono: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
```

Mono is only for decorative zone labels in the showcase scene. In the product, figures are set in
the main family with `font-variant-numeric: tabular-nums`.

### Type scale

| Token | Size / line height / weight / tracking | Application |
| --- | --- | --- |
| `--text-display` | 48 / 1.05 / 700 / -0.03em | showcase H1 on desktop |
| `--text-display-sm` | 32 / 1.1 / 700 / -0.025em | showcase H1 below 640 px |
| `--text-stat` | 24 / 1.1 / 700 / -0.02em | KPI values, showcase figures |
| `--text-heading` | 20 / 1.25 / 600 / -0.02em | section headings |
| `--text-title` | 16 / 1.3 / 600 / -0.01em | card headings, showcase subheadings |
| `--text-body-lg` | 16 / 1.6 / 400 / 0 | showcase intro copy, long empty states |
| `--text-body` | 14 / 1.6 / 400 / 0 | body text |
| `--text-ui` | 14 / 1.4 / 500 / 0 | buttons, fields, controls |
| `--text-small` | 13 / 1.45 / 400 / 0.005em | table cells, metadata, hints |
| `--text-caption` | 12 / 1.4 / 500 / 0.01em | field labels, badges, micro-labels |
| `--text-micro` | 11 / 1.2 / 600 / 0.12em, uppercase, mono | scene labels on the showcase only |

Rules:

1. **12 px is the floor in the product.** `--text-micro` is the only exception, and only inside
   the decorative scene with `aria-hidden`.
2. **Sentence case only in the product.** No all-caps headings, no stretched overlines. The
   showcase overline is the only allowed all-caps element outside the scene.
3. **Tabular figures** in statistics, tables, amounts, timers, and confidence percentages.
4. **Line length** under 75 characters for readable text; left-aligned, never justified.
5. **Dates** everywhere: `11 Nov 2026` — in fields, tables, history, and toasts. The native `date`
   input stores an ISO value internally.
6. **Money** everywhere: `8,600.00 ₴` — amount, space, symbol; currency never lives only in the
   adjacent field. The code (`UAH`) goes in a tooltip or a record, not in the cell.
7. **File names** are plain text, not mono; they truncate in the middle and keep the extension.

The current inconsistency this removes: 30 different font sizes across two pages (0.5–3.05rem),
10.4 px field labels, 11.5 px table headings, 10.6 px hints.

## 4. Spacing

A 4 px grid. Every gap, padding, and section rhythm comes from this scale; arbitrary values
(`15px`, `22px`, `26px`, `46px`) are migration debt.

| Token | Value | Application |
| --- | --- | --- |
| `--space-1` | 4 | dot to text, icon offset |
| `--space-2` | 8 | icon to label, chip gap, tight stack |
| `--space-3` | 12 | field padding, control inner gap, compact stack |
| `--space-4` | 16 | card body padding on mobile, field grid gap, list gap |
| `--space-5` | 20 | card body padding, gap between cards, internal section rhythm |
| `--space-6` | 24 | page gutters, section rhythm, card header padding |
| `--space-8` | 32 | card to card in a column, hero padding |
| `--space-10` | 40 | empty-state padding, block separation |
| `--space-12` | 48 | showcase section separation |
| `--space-16` | 64 | showcase hero separation |
| `--space-20` | 80 | showcase top and bottom rhythm |

Application:

- Page gutters: 24 on desktop, 16 below 560 px.
- Product card gap: 20; card body padding: 20; card header height: 56
  (padding 0 20).
- Showcase: hero padding 32/24, scene padding 24, section rhythm 48–64.
- Vertical rhythm inside cards: heading → body 8, body → actions 20, field rows 16.

## 5. Shape and surfaces

| Token | Value | Application |
| --- | --- | --- |
| `--radius-xs` | 6 | icon tiles, small tags, document preview |
| `--radius-sm` | 10 | buttons, fields, chips, small controls |
| `--radius-md` | 12 | nested panels, route cards, toasts |
| `--radius-lg` | 16 | cards |
| `--radius-xl` | 24 | showcase scene and marketing panels |
| `--radius-pill` | 999 | pills, statuses, sample chips |

- Base border: `1px solid var(--color-line)`; accent border: `--color-line-strong`.
- Elevation relies mostly on thin lines: `--shadow-1: 0 8px 24px -16px rgba(0,0,0,.9)` for
  raised surfaces, `--shadow-2: 0 20px 50px -20px rgba(0,0,0,.9)` for overlays and
  toasts.
- Glows (`--glow-accent`, `--glow-attention`) are only for the showcase hero. The workspace has
  no glows except the focus ring and a brief pulse during confirmation.
- Avoid uniform rounded rectangles: radius encodes hierarchy (control 10 → nested panel 12 →
  card 16 → scene 24).

## 6. Motion

| Token | Value | Application |
| --- | --- | --- |
| `--dur-1` | 120ms | hover, press, focus |
| `--dur-2` | 180ms | state change, toast in and out, color transitions |
| `--dur-3` | 260ms | panel or row disclosure |
| `--dur-4` | 800ms | one step of the showcase choreography (the router scene runs on its own timeline, ~5.8 s per cycle) |
| `--ease-out` | `cubic-bezier(.22,.8,.28,1)` | entrances, state changes |
| `--ease-in-out` | `cubic-bezier(.4,0,.2,1)` | movement between two states |
| `--ease-spring` | `cubic-bezier(.34,1.15,.64,1)` | press feedback and marketing |

Rules:

1. **In the product, only what responds to a human is animated**: hover, press, focus, toast, row
   disclosure, spinner. No entrance animations, no scroll reveals, no looping decoration.
2. **The showcase has one choreographed moment** — the router scene cycle. Everything else on the
   showcase is static or a hover state.
3. Only `transform` and `opacity` are animated; layout properties are never animated.
4. `prefers-reduced-motion: reduce` turns off everything optional: the scene draws its final
   state, transitions become instant, the spinner remains.
5. Any animation in the product longer than 260 ms needs a reason at review.

### Router scene on the showcase

The scene explains the pipeline; it does not decorate the hero. It has one role —
**explanation** — and one run that can be stopped.

- The flow is shown by a laser: the line is not drawn in advance — the beam draws it during the
  run. At the start of the cycle there is no line in the scene; the beam leaves the inbox, passes
  through reading and review, splits into three routes, and converges in the CRM. After the hold,
  the line fades out with the scenes.
- Intake holds four documents of three types (invoice, act, delivery note) with an amount and a
  source. Routing is visible in the route: Payments gets an invoice by type, Accounting gets the
  act and the delivery note by type, Approval gets the 192,000 UAH invoice by amount rule
  (≥ 100,000). Routes light up in turn, each with its own documents and reason.
- Cycle timeline: 620 ms documents enter, 300 ms laser gathers the inbox, 240 ms bus,
  2,100 ms pass through reading and review, 670 ms split into three routes at 200 ms each,
  340 ms merge and CRM, 1,500 ms hold, 450 ms reset, 600 ms pause. Total ≈ 6.8 s.
- Mobile (≤ 1100): the line is not drawn, the scene stacks into a column, and the same timeline
  lights the stages in turn; block order and labels carry the history.
- Controls: a Pause/Play button next to the label; the run starts only when the scene is in view
  (`IntersectionObserver`) and stops when the tab is hidden. This is a WCAG 2.2.2 requirement for
  auto-starting motion longer than five seconds.
- `prefers-reduced-motion: reduce`: no beam or moving head; the final diagram appears immediately —
  the line fully drawn, four documents, reading, confirmation, three routes with reasons, deals. The
  control button is hidden.
- Only `transform` and `opacity` animate; the line is drawn with `stroke-dashoffset` (repaint only,
  no layout); background glows are static, and there are no infinite decorative animations. The
  implementation is in `templates/landing.html`, block `#scene`.

## 7. Grid

| Token | Value |
| --- | --- |
| `--container` | 1320px (both pages) |
| `--gutter` | 24px on desktop / 16px below 560px |
| `--topbar-height` | 60px on desktop / 56px on mobile |
| `--z-topbar` | 20 |
| `--z-overlay` | 100 |

Breakpoints: 560 (mobile), 960 (the workspace stacks into a column), 1100 (the showcase scene
stacks), 1280 (wide gutters). CSS is mobile-first, but the workspace is designed for a laptop
first; on mobile, review must still work without horizontal scrolling.

Grid: the workspace has two columns, document 52% / fields 48%, stacking below 960. The queue is
the entry point, and it must be visible without scrolling through the document (see the audit,
decision D5). The showcase is a centered column: hero → scene → numbers → footer, one idea per
screen.

## 8. Components

### Buttons

Sizes: `sm` 32 px, `md` 40 px, `lg` 44 px (the primary button on touch devices). Padding:
12/16/20. Radius `--radius-sm`. Label `--text-ui`, one weight above body (600).

| Variant | Default | Hover | Press | Application |
| --- | --- | --- | --- | --- |
| Primary | `--color-accent-fill`, white label | `--color-accent-fill-hover` | `--color-accent-fill-press` + 1px shift | the only action that moves the document forward |
| Secondary | `--color-surface-raised`, line border, `--color-text` | `--color-surface-hover` | `--color-line-strong` | alternative actions |
| Ghost | transparent, `--color-text-2` | `--color-surface-hover` | soft fill | topbar, reset |

There is no warm amber button in the system: by decision D1 (16.11), the showcase and the product
have one action — purple — and the hero's warm glow is atmosphere only.

Every state has a visible focus ring: `outline: 2px solid var(--color-accent); outline-offset: 2px`.
Disabled: 45% opacity, no hover, `cursor: not-allowed`. Loading: a spinner replaces the label, and
the button width does not change.

### Fields

Height 40 on desktop / 44 on mobile, radius `--radius-sm`, background `--color-surface-raised`,
border `--color-line`, label `--text-caption` above, value `--text-body`. States: focus (accent
border + soft ring), low confidence (attention border + dot), missing (error border + message),
disabled (surface, text-3). State is never conveyed by border color alone: a confidence dot and
percentage sit next to it.

### Cards

Background `--color-surface`, border `--color-line`, radius `--radius-lg`. Header 56 px with a
`--text-title` heading and a `--text-caption` hint on the right; body with 20 padding. A card
groups one task; a page has at most one primary card per column.

### Chips and pills

Sample chips: height 32, pill radius, `--text-caption`, transparent background, line border; hover
adds `--color-accent-soft` and an accent border. Status pills: 20 px, `--text-caption`, one of five
semantic fills; every pill has a text label.

### Confidence indicator

6 px dot + tabular percentage next to the field label. Thresholds: high ≥ 80% — success, medium
50–79% — attention, low < 50% — error. The percentage is always shown; a tooltip explains where
the number comes from. The header of the fields card summarizes: "3 fields below 70%".

### Tables

Header `--text-caption` in `--color-text-3`, sentence case, 12 px; row height 44; cells
`--text-small`; numeric cells right-aligned and tabular; thin row dividers. A table longer than the
screen gets a sticky header, a filter, and pagination.

### Toasts

Bottom center, 24 px from the bottom, radius `--radius-md`, `--text-body`, `--shadow-2`, border by
state (success/attention/error), auto-hide after 3.2 s, never the only signal of a critical result.

### Icons

Inline SVG only, 16/20/24/32 px, 1.5 stroke, currentColor. No emoji or Unicode glyphs (`⇪`, `✓`
are placeholders, not an icon set). Icons for destructive actions and statuses always come with
text.

## 9. Baseline accessibility requirements

- Text contrast ≥ 4.5:1; meaning-bearing non-color indicators (focus ring, status dot, confidence
  border, error border) ≥ 3:1; thin dividers never encode state.
- Touch targets ≥ 44×44 on mobile; pointer targets ≥ 32.
- Keyboard: every interactive element is a `<button>` or `<a>`; chips and the drop zone become
  buttons; no focus trap; tab order matches visual order.
- Visible focus on every focusable element; never `outline: none` without a replacement.
- `prefers-reduced-motion` is respected; the app remains usable without animations.
- Never convey meaning by color alone; no hover-only affordances.
- Text is readable at 200% zoom without horizontal scrolling.
- Form errors are announced (`aria-live`) and tied to the field.

## 10. Text rules

- Sentence case only; active voice; buttons start with a verb; one name per action across the flow
  ("Save document" → "Document saved").
- Name things the way the user controls them: "mail", "review queue", "destination", not "IMAP
  intake" and "routing policy".
- Empty states say what is missing and what to do next; errors say what happened and how to fix
  it, in the interface's voice.
- "Document" is the generic word; "invoice", "act", and "delivery note" are specific types. The
  drop zone and empty states say "document".
- No all-caps and no chains of decorative separators (`·`) in the product; one fact per element.

## 11. Decisions to make

| # | Decision | Recommendation | Consequence |
| --- | --- | --- | --- |
| D1 | Primary accent — **accepted 16.11** | purple action fill everywhere; amber CTA removed | showcase repainted purple; the hero's warm glow remains as atmosphere |
| D2 | Font file | ship variable Inter (Latin + Cyrillic, OFL) | +~300 KB, predictable rendering, no CDN |
| D3 | Container | one 1320 px container for both pages | the showcase loses 20 px of width |
| D4 | Theme | dark only | no light-theme work planned |
| D5 | Queue placement | the queue becomes the entry point: top of the workspace or a persistent side column | the work screen layout changes |
| D6 | Icons | an inline SVG set replaces glyphs and emoji | a small asset set, consistent states |

## 12. Rollout plan

1. Add `templates/tokens.css` with the block below; link it in both templates and remove their
   inline `:root`.
2. Replace the 30 font sizes with scale tokens; raise every product label to ≥ 12 px.
3. Replace the gray zoo (`#8f95ab`, `#7d8399`, `#6f768d`, `#5d6377`, `#b4b9cb`…) with four text
   tokens; check every empty state against `--color-text-4`.
4. Replace the radius zoo (5, 6, 8, 9, 10, 11, 12, 14, 16, 26) with six radius tokens.
5. Move page gutters, card padding, and section rhythm onto the spacing scale; remove odd values
   (15, 22, 26, 46).
6. Merge the topbar of both pages into one component with the same height and padding.
7. Mobile pass: 44 px targets, no horizontal scrolling, the primary action reachable.
8. Lock the decisions above, then fix the audit findings in its order, using tokens as the link
   between design and code.

## 13. Token block

```css
:root {
  /* surfaces */
  --color-bg: #07080d;
  --color-surface: #0e1018;
  --color-surface-raised: #12141f;
  --color-surface-hover: #181b29;
  --color-line: rgba(255,255,255,.09);
  --color-line-strong: rgba(255,255,255,.16);

  /* text */
  --color-text: #eceef5;
  --color-text-2: #b4b9cb;
  --color-text-3: #9aa0b4;
  --color-text-4: #7f8599;

  /* action and semantics */
  --color-accent: #7c5cff;
  --color-accent-fill: #7454ff;
  --color-accent-fill-hover: #6f4cf5;
  --color-accent-fill-press: #6445ee;
  --color-accent-text: #b9a7ff;
  --color-accent-soft: rgba(124,92,255,.14);
  --color-on-accent: #fff;
  --color-attention: #fbbf24;
  --color-attention-text: #fcd34d;
  --color-attention-soft: rgba(251,191,36,.14);
  --color-on-attention: #221604;
  --color-flow: #22d3ee;
  --color-flow-soft: rgba(34,211,238,.12);
  --color-success: #34d399;
  --color-success-text: #6ee7b7;
  --color-success-soft: rgba(52,211,153,.12);
  --color-danger: #f87171;
  --color-danger-text: #fca5a5;
  --color-danger-soft: rgba(248,113,113,.12);

  /* typography */
  --font-sans: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
               "Helvetica Neue", Arial, sans-serif;
  --font-mono: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  --text-display: 700 48px/1.05 var(--font-sans);
  --text-display-sm: 700 32px/1.1 var(--font-sans);
  --text-stat: 700 24px/1.1 var(--font-sans);
  --text-heading: 600 20px/1.25 var(--font-sans);
  --text-title: 600 16px/1.3 var(--font-sans);
  --text-body-lg: 400 16px/1.6 var(--font-sans);
  --text-body: 400 14px/1.6 var(--font-sans);
  --text-ui: 500 14px/1.4 var(--font-sans);
  --text-small: 400 13px/1.45 var(--font-sans);
  --text-caption: 500 12px/1.4 var(--font-sans);
  --text-micro: 600 11px/1.2 var(--font-mono);

  /* spacing */
  --space-1: 4px;
  --space-2: 8px;
  --space-3: 12px;
  --space-4: 16px;
  --space-5: 20px;
  --space-6: 24px;
  --space-8: 32px;
  --space-10: 40px;
  --space-12: 48px;
  --space-16: 64px;
  --space-20: 80px;

  /* shape */
  --radius-xs: 6px;
  --radius-sm: 10px;
  --radius-md: 12px;
  --radius-lg: 16px;
  --radius-xl: 24px;
  --radius-pill: 999px;
  --shadow-1: 0 8px 24px -16px rgba(0,0,0,.9);
  --shadow-2: 0 20px 50px -20px rgba(0,0,0,.9);
  --glow-accent: 0 0 24px -6px rgba(124,92,255,.55);
  --glow-attention: 0 0 28px -8px rgba(251,191,36,.55);
  --focus-ring: 2px solid var(--color-accent);

  /* motion */
  --dur-1: 120ms;
  --dur-2: 180ms;
  --dur-3: 260ms;
  --dur-4: 800ms;
  --ease-out: cubic-bezier(.22,.8,.28,1);
  --ease-in-out: cubic-bezier(.4,0,.2,1);
  --ease-spring: cubic-bezier(.34,1.15,.64,1);

  /* layout */
  --container: 1320px;
  --gutter: 24px;
  --topbar-height: 60px;
  --z-topbar: 20;
  --z-overlay: 100;
}
```
