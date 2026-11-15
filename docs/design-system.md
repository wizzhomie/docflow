# DocFlow design system

Version 0.1 · 2026-11-16 · scope: the showcase page (`/`) and the review workspace (`/app`).

This is the single source of truth for color, type, spacing, shape, motion, layout and
component rules. It exists so that the two pages stop drifting from each other and so every
later change has a number to point at instead of a taste argument. A rendered specimen of
every token lives next to this file: [`design-system.html`](design-system.html).

## 1. Direction

DocFlow is a deterministic router for business documents: a file arrives, the system reads it,
a human confirms it, and the document is delivered to the process and CRM that own it. The
interface should feel like a precise instrument for that one job, not like a dashboard.

- **The signature is the route.** A document's path from intake to destination is the product's
  own visual language: a connector, a destination label, a reason. Spend the boldness on the
  landing router scene; the workspace stays quiet and precise.
- **One accent, one meaning.** Violet means action and decision, amber means a human has to look,
  cyan means destination and flow, green means confirmed, red means blocked. A color is never
  decoration in the product.
- **Data is the personality.** Numbers are tabular, amounts carry their currency, dates read the
  same everywhere. Typography stays neutral so the document data carries the page.
- **Density is honest.** A reviewer scans fields and queue rows; the interface is dense but
  never below 12 px and never below a 44 px touch target on mobile.

Deliberately not in this system: a light theme, a second typeface, a decorative icon set, an
LLM-styled "assistant" surface. None of them serve the pipeline.

## 2. Color

Dark theme only. The base is near-black so a document preview (which is white paper) is the
brightest object on the screen.

### Surfaces and lines

| Token | Value | Use |
| --- | --- | --- |
| `--color-bg` | `#07080d` | page background |
| `--color-surface` | `#0e1018` | cards, panels |
| `--color-surface-raised` | `#12141f` | inputs, nested panels |
| `--color-surface-hover` | `#181b29` | row and card hover |
| `--color-line` | `rgba(255,255,255,.09)` | default hairline |
| `--color-line-strong` | `rgba(255,255,255,.16)` | emphasized divider, borders that carry meaning |

### Text

| Token | Value | Contrast on `--color-surface` | Use |
| --- | --- | --- | --- |
| `--color-text` | `#eceef5` | 16.4:1 | headings, values, primary copy |
| `--color-text-2` | `#b4b9cb` | 9.7:1 | body copy, secondary values |
| `--color-text-3` | `#9aa0b4` | 7.3:1 | labels, hints, metadata |
| `--color-text-4` | `#7f8599` | 5.2:1 | empty states, timestamps, least important text |

`--color-text-4` is the floor: it replaces the old `#5d6377` (3.2:1), which failed WCAG AA for
every size it was used at.

### Accents and semantics

| Token | Value | Meaning | Use |
| --- | --- | --- | --- |
| `--color-accent` | `#7c5cff` | brand / decision | focus rings, selected borders, brand mark, links |
| `--color-accent-fill` | `#7454ff` | action fill | primary button background (white label, 4.7:1) |
| `--color-accent-fill-hover` | `#6f4cf5` | action fill, hover | deepens on hover |
| `--color-accent-fill-press` | `#6445ee` | action fill, pressed | deepens further |
| `--color-accent-text` | `#b9a7ff` | accent text | links and accent labels on dark (9.1:1) |
| `--color-accent-soft` | `rgba(124,92,255,.14)` | selected tint | selected/active row tint, focus ring fill |
| `--color-attention` | `#fbbf24` | human attention | low-confidence field, "needs review", warnings |
| `--color-attention-text` | `#fcd34d` | attention text | attention copy on dark (13.2:1) |
| `--color-attention-soft` | `rgba(251,191,36,.14)` | attention tint | warning surface |
| `--color-on-attention` | `#221604` | text on amber | amber-filled badges and marketing CTA (10.6:1) |
| `--color-flow` | `#22d3ee` | destination / flow | route destination names, flow lines (10.5:1) |
| `--color-flow-soft` | `rgba(34,211,238,.12)` | flow tint | parsed-field chips, route glow |
| `--color-success` | `#34d399` | confirmed / safe | saved, high confidence, auto-pass |
| `--color-success-text` | `#6ee7b7` | success text | success copy on dark (12.5:1) |
| `--color-success-soft` | `rgba(52,211,153,.12)` | success tint | confirmed surface |
| `--color-danger` | `#f87171` | blocked / failed | CRM error, missing value, destructive |
| `--color-danger-text` | `#fca5a5` | danger text | error copy on dark (10.0:1) |
| `--color-danger-soft` | `rgba(248,113,113,.12)` | danger tint | error surface |
| `--color-on-accent` | `#ffffff` | text on violet | primary button label |

Rules:

1. **One accent per meaning.** If something is a decision, it is violet. If a human must look, it
   is amber. Never use amber to mean "action" in the product — the review screen already reads
   amber as "check this".
2. **Amber is not a CTA color inside the product.** The landing page may keep its warm hero glow
   as atmosphere, but the CTA follows the same violet action fill as the product (decision D1).
3. **Never color alone.** Confidence is a dot plus a percentage plus a border; a route is a label
   plus an arrow, not just a cyan word.
4. Semantic colors are reserved: no decorative green, amber or cyan glows in the workspace.
5. All text meets 4.5:1. Non-text indicators that carry meaning — focus rings, status dots,
   confidence and error borders — meet 3:1 against the surface they sit on. Hairline separators are
   decorative and never encode a state on their own.

## 3. Typography

One family: **Inter** for UI and marketing copy, with a neutral fallback stack. The identity comes
from the route language and color, not from a display face; a second family would only compete
with the document data (decision D2: self-host Inter as a woff2 asset, Latin + Cyrillic).

```css
--font-sans: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
             "Helvetica Neue", Arial, sans-serif;
--font-mono: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
```

The mono family is only for the decorative zone labels in the landing router scene. In the
product, numbers use the sans family with `font-variant-numeric: tabular-nums`.

### Scale

| Token | Size / line / weight / tracking | Use |
| --- | --- | --- |
| `--text-display` | 48 / 1.05 / 700 / -0.03em | landing H1 on desktop |
| `--text-display-sm` | 32 / 1.1 / 700 / -0.025em | landing H1 below 640 px |
| `--text-stat` | 24 / 1.1 / 700 / -0.02em | KPI values, landing stats |
| `--text-heading` | 20 / 1.25 / 600 / -0.02em | section headings |
| `--text-title` | 16 / 1.3 / 600 / -0.01em | card titles, landing subheadings |
| `--text-body-lg` | 16 / 1.6 / 400 / 0 | landing sub-copy, long empty states |
| `--text-body` | 14 / 1.6 / 400 / 0 | body copy |
| `--text-ui` | 14 / 1.4 / 500 / 0 | buttons, inputs, controls |
| `--text-small` | 13 / 1.45 / 400 / 0.005em | table cells, metadata, hints |
| `--text-caption` | 12 / 1.4 / 500 / 0.01em | field labels, badges, micro-labels |
| `--text-micro` | 11 / 1.2 / 600 / 0.12em, uppercase, mono | landing scene labels only |

Rules:

1. **12 px is the floor in the product.** `--text-micro` is the one exception and only inside the
   decorative, `aria-hidden` landing scene.
2. **Sentence case everywhere in the product.** No all-caps labels, no tracked-out eyebrows. The
   landing eyebrow is the single allowed uppercase element outside the scene.
3. **Tabular numbers** in stats, tables, amounts, timers and confidence percentages.
4. **Line length** stays under 75 characters for reading copy; never justify.
5. **Dates** everywhere: `11 Nov 2026` — in fields, tables, history and toasts. The native date
   input keeps its ISO value internally.
6. **Money** everywhere: `8,600.00 ₴` — amount, space, symbol; the currency never lives only in a
   neighbouring field. The code (`UAH`) is available in a tooltip or the record, not in the cell.
7. **File names** are regular text, not mono, and truncate in the middle with the extension kept.

Current drift to remove: 30 distinct font sizes between the two pages (0.5–3.05rem), field labels
at 10.4 px, table headers at 11.5 px, hints at 10.6 px.

## 4. Spacing

A 4-point base grid. Every gap, padding and section rhythm comes from this scale; arbitrary
values (`15px`, `22px`, `26px`, `46px`) are migration debt.

| Token | Value | Use |
| --- | --- | --- |
| `--space-1` | 4 | dot-to-text, icon nudge |
| `--space-2` | 8 | icon-to-label, chip gap, tight stack |
| `--space-3` | 12 | field padding, control inner gap, compact stack |
| `--space-4` | 16 | card body padding on mobile, field grid gap, list gap |
| `--space-5` | 20 | card body padding, card grid gap, section inner rhythm |
| `--space-6` | 24 | page gutter, section rhythm, card header padding |
| `--space-8` | 32 | card-to-card in a column, hero padding |
| `--space-10` | 40 | empty-state padding, block separation |
| `--space-12` | 48 | landing section separation |
| `--space-16` | 64 | landing hero separation |
| `--space-20` | 80 | landing top/bottom rhythm |

Application:

- Page gutter: 24 on desktop, 16 below 560 px.
- Product card gap: 20; card body padding: 20; card header height: 56 (padding 0 20).
- Landing: hero padding 32/24, scene padding 24, section rhythm 48–64.
- Vertical rhythm inside cards: title → body 8, body → actions 20, field rows 16.

## 5. Shape and surfaces

| Token | Value | Use |
| --- | --- | --- |
| `--radius-xs` | 6 | icon tiles, small tags, document thumb |
| `--radius-sm` | 10 | buttons, inputs, chips, small controls |
| `--radius-md` | 12 | inner panels, route cards, toasts |
| `--radius-lg` | 16 | cards |
| `--radius-xl` | 24 | landing scene and marketing panels |
| `--radius-pill` | 999 | pills, status tags, sample chips |

- Default border: `1px solid var(--color-line)`; emphasized: `--color-line-strong`.
- Elevation is mostly hairlines: `--shadow-1: 0 8px 24px -16px rgba(0,0,0,.9)` for raised
  surfaces, `--shadow-2: 0 20px 50px -20px rgba(0,0,0,.9)` for overlays and toasts.
- Glows (`--glow-accent`, `--glow-attention`) belong to the marketing hero only. The workspace has
  no glow except the focus ring and the transient processing pulse of the confirm action.
- No rounded-rectangle sameness: radius encodes hierarchy (control 10 → inner panel 12 → card 16
  → scene 24).

## 6. Motion

| Token | Value | Use |
| --- | --- | --- |
| `--dur-1` | 120ms | hover, press, focus |
| `--dur-2` | 180ms | state change, toast in/out, color transitions |
| `--dur-3` | 260ms | panel or row expand/collapse |
| `--dur-4` | 800ms | landing hero choreography (one loop) |
| `--ease-out` | `cubic-bezier(.22,.8,.28,1)` | entrances, state changes |
| `--ease-in-out` | `cubic-bezier(.4,0,.2,1)` | movement between two states |
| `--ease-spring` | `cubic-bezier(.34,1.15,.64,1)` | press feedback and marketing only |

Rules:

1. **The product animates only what answers a person**: hover, press, focus, toast, row open,
   spinner. No entrance animations, no scroll reveals, no looping decoration.
2. **Marketing gets one orchestrated moment** — the router scene loop. Everything else on the
   landing page is static or a hover state.
3. Animate `transform` and `opacity` only; never animate layout properties.
4. `prefers-reduced-motion: reduce` stops every non-essential animation: the scene renders its
   final state, transitions become instant, the spinner remains.
5. Any animation over 260 ms in the product needs a reason in review.

## 7. Layout

| Token | Value |
| --- | --- |
| `--container` | 1320px (both pages) |
| `--gutter` | 24px desktop / 16px below 560px |
| `--topbar-height` | 60px desktop / 56px mobile |
| `--z-topbar` | 20 |
| `--z-overlay` | 100 |

Breakpoints: 560 (mobile), 960 (workspace stacks to one column), 1100 (landing scene stacks),
1280 (wide spacing). Mobile-first CSS, but the workspace is designed for a laptop first; mobile
must still complete a review without a horizontal scroll.

Grid: the workspace is two columns, document 52% / fields 48%, stacking below 960. The queue is
the entry point and must be visible without scrolling past a document (see the audit, decision
D5). The landing page is a centered column: hero → scene → stats → footer, one idea per screen.

## 8. Components

### Buttons

Sizes: `sm` 32 px, `md` 40 px, `lg` 44 px (primary on touch). Padding: 12/16/20. Radius `--radius-sm`.
Label `--text-ui`, one weight step above body (600).

| Variant | Default | Hover | Pressed | Use |
| --- | --- | --- | --- | --- |
| Primary | `--color-accent-fill`, white label | `--color-accent-fill-hover` | `--color-accent-fill-press` + 1px down | the one action that moves the document forward |
| Secondary | `--color-surface-raised`, line border, `--color-text` | `--color-surface-hover` | line-strong | alternate actions |
| Ghost | transparent, `--color-text-2` | `--color-surface-hover` | tint | topbar, reset |
| Marketing warm | amber gradient, `--color-on-attention` | lighter gradient | 1px down | landing CTA only while D1 is open; removed if D1 unifies on violet |

Every state has a visible focus ring: `outline: 2px solid var(--color-accent); outline-offset: 2px`.
Disabled: 45% opacity, no hover, `cursor: not-allowed`. Loading: spinner replaces the label and the
button keeps its width.

### Inputs

Height 40 desktop / 44 mobile, radius `--radius-sm`, background `--color-surface-raised`, border
`--color-line`, label `--text-caption` above, value `--text-body`.
States: focus (accent border + soft ring), low confidence (attention border + dot), missing (danger
border + message), disabled (surface, text-3). Never signal state with border color alone: pair it
with the confidence dot and the percentage.

### Cards

Background `--color-surface`, border `--color-line`, radius `--radius-lg`. Header 56 px with a
`--text-title` title and a `--text-caption` hint on the right; body padding 20. A card groups one
job; a page has at most one primary card per column.

### Chips and pills

Sample chips: 32 px high, pill radius, `--text-caption`, transparent background, line border; hover
adds `--color-accent-soft` and an accent border. Status pills: 20 px, `--text-caption`, one of the
five semantic tints; every pill has a text label.

### Confidence indicator

6 px dot + 12 px tabular percentage next to the field label. Thresholds: high ≥ 80% success, mid
50–79% attention, low < 50% danger. The percentage is always shown; a tooltip explains what the
number comes from. The fields card header summarises: "3 fields below 70%".

### Tables

Header `--text-caption` in `--color-text-3`, sentence case, 12 px; row height 44; cells
`--text-small`; numeric cells right-aligned, tabular; hairline row separators. A table over one
screen gets a sticky header, a filter and pagination.

### Toasts

Bottom center, 24 px from the bottom, radius `--radius-md`, `--text-body`, `--shadow-2`, state
border (success/attention/danger), auto-dismiss after 3.2 s, never the only signal for a critical
result.

### Icons

Inline SVG only, 16/20/24/32 px, 1.5 stroke, currentColor. No emoji or unicode glyphs (`⇪`, `✓` are
placeholders, not an icon system). Destructive and status icons always come with text.

## 9. Accessibility floor

- Text contrast ≥ 4.5:1; meaningful non-text indicators (focus ring, status dot, confidence border,
  error border) ≥ 3:1; hairline separators never encode a state.
- Touch targets ≥ 44×44 on mobile; pointer targets ≥ 32.
- Keyboard: every interactive element is a `<button>` or `<a>`; chips and the drop zone become
  buttons; no keyboard trap; tab order follows the visual order.
- Visible focus on every focusable element; never `outline: none` without a replacement ring.
- `prefers-reduced-motion` respected; the app remains usable with animations off.
- No color-only meaning; no hover-only affordance.
- Text remains readable at 200% zoom without horizontal scrolling.
- Form errors are announced (`aria-live`) and tied to the field.

## 10. Content rules

- Sentence case; active voice; verb-first buttons; one name per action through the whole flow
  ("Save document" → "Document saved").
- Name things by what the user manages: "mailbox", "review queue", "destination" — not "IMAP
  intake" or "routing policy".
- Empty states say what is missing and what to do next; errors say what happened and how to fix it,
  in the interface's voice.
- "Document" is the generic noun; "invoice", "act" and "delivery note" are the specific types.
  The drop zone and empty states say "document".
- No all-caps, no decorative separators (`·`) chains in the product; one fact per element.

## 11. Decisions to take

| # | Decision | Recommendation | Consequence |
| --- | --- | --- | --- |
| D1 | Primary accent | violet action fill everywhere; amber stays "a human must look" | landing CTA changes from amber to violet; warm glow stays as atmosphere |
| D2 | Typeface asset | self-host Inter variable (Latin + Cyrillic, OFL) | +~300 KB, deterministic rendering, no CDN |
| D3 | Container | one 1320 px container for both pages | landing loses 20 px of width |
| D4 | Theme | dark only | no light theme work |
| D5 | Queue placement | queue becomes the entry point: top of the workspace or a persistent side column | layout change of the workspace page |
| D6 | Icons | an inline SVG set replaces glyphs and emoji | small asset set, consistent states |

## 12. Migration plan

1. Add `templates/tokens.css` with the block below; link it from both templates and delete their
   inline `:root` blocks.
2. Replace the 30 font sizes with the scale tokens; raise every product label to ≥ 12 px.
3. Replace the gray zoo (`#8f95ab`, `#7d8399`, `#6f768d`, `#5d6377`, `#b4b9cb`…) with the four text
   tokens; check every empty state against `--color-text-4`.
4. Replace the radius zoo (5, 6, 8, 9, 10, 11, 12, 14, 16, 26) with the six radius tokens.
5. Move page gutters, card padding and section rhythm to the spacing scale; remove odd values
   (15, 22, 26, 46).
6. Unify the topbar in both pages into one component with the same height and padding.
7. Mobile pass: 44 px targets, no horizontal scroll, the primary action stays reachable.
8. Register the decisions above, then fix the audit findings in the order of the audit, with the
   tokens as the mapping between the design and the code.

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

  /* type */
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
