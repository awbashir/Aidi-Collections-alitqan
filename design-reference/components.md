# Aidi Enterprise — Component specs

Every component here is the same component as in the consumer Aidi Design System, re-specced.
The API does not change — only the tokens it resolves. Values are exact.

---

## Buttons

Height **36px**, radius **6px**, font **13.5px / 800**, horizontal padding **18px**,
gap between icon and label **8px**. Small variant: 32px height, 14px padding, 12.5px text.

| Variant | Rest | Hover | Active | Use |
| --- | --- | --- | --- | --- |
| Primary | bg `#070A4B`, text `#fff` | bg `#14184F` | bg `#04062F` | The default. One or two per view. |
| Secondary | bg `#fff`, 1px `#B8C4CE`, text `#070A4B` | bg `#F2F6F8` | bg `#E7EEF2` | Paired with primary. |
| Tertiary | transparent, text `#234BB0` | text `#1A3A8A`, bg `#EEF3FD` | bg `#D9E4FA` | Inline, low-stakes. |
| Destructive | bg `#B3261E`, text `#fff` | bg `#8F1E17` | bg `#6E1712` | Delete, revoke, block. |
| Signal | bg `#FF6600`, text `#fff` | bg `#E65B00` | bg `#CC5200` | **Max one per screen.** |
| Disabled | bg `#F2F6F8`, 1px `#D7E1E8`, text `#8A929F` | — | — | No shadow, no pointer. |

**Focus** on every variant: `box-shadow: 0 0 0 2px #fff, 0 0 0 4px #2F5FD6`. Never orange —
the orange ring collided with orange CTAs and read as an error.

`Signal` is reserved for the action the page exists to produce — "Run query", "Approve
release". If the page has no such action, it has no signal button.

On dark theme, primary becomes azure `#2F5FD6`; navy disappears into the `#060820` page.

---

## Fields

Height **36px**, radius **6px**, border 1px `#B8C4CE`, inline padding **12px**, text **14px**
`#2E3450`, background `#fff`. Label above: **12.5px / 800** `#070A4B`, gap 7px. Helper text
below: **12px** `#6B7482`, gap 7px.

| State | Border | Extra |
| --- | --- | --- |
| Rest | `#B8C4CE` | — |
| Hover | `#8A929F` | — |
| Focus | `#2F5FD6` | `box-shadow: 0 0 0 2px rgba(47,95,214,.22)`; helper text turns `#234BB0` |
| Error | `#B3261E` | helper text `#B3261E` and states the fix: "Select a workspace to continue" |
| Read-only | `#D7E1E8` | bg `#F2F6F8`, text `#8A929F`, label `#8A929F`, tabular if it's an ID |
| Disabled | `#D7E1E8` | bg `#F2F6F8`, text `#8A929F`, no pointer |

**Select** — same box, chevron `#8A929F` at 11px on the trailing edge, `justify-content: space-between`.
**With affix** — 15px Lucide icon `#8A929F` on the leading edge, 8px gap.
**Textarea** — same border and radius, `min-height: 88px`, `padding: 10px 12px`, 1.55 leading.

---

## Status & badges

Radius **999px** — this is the one place pills survive. Height **24px** (22px inside table
rows), padding `0 11px 0 9px`, text **12px / 800**, glyph gap 7px.

Every status carries a **distinct glyph shape** as well as a colour, so the set survives
greyscale printing and colour-vision deficiency:

| Status | Text / bg | Glyph |
| --- | --- | --- |
| Answered | `#0F7A52` / `#E4F2EC` | 7px circle, filled |
| Clarifying | `#B25A00` / `#FBEBDD` | 7px square rotated 45° (diamond) |
| Blocked | `#B3261E` / `#F9E6E4` | 8×2px bar |
| In review | `#234BB0` / `#E7EDFA` | 7px circle, 2px ring, hollow |
| Archived | `#6B7482` / `#E7EEF2` | 7px circle, `#8A929F` |

**Metadata tag** (not a status): radius **4px**, height 24px, padding `0 11px`, bg `#070A4B`,
text `#fff` **11px / 800 / +0.06em**. For classifications like `PII masked`, `Restricted`.

---

## Navigation

### Tabs
Underline tabs on a 1px `#D7E1E8` rule. Padding `0 16px 11px`. Active: **13.5px / 800**
`#070A4B` with `box-shadow: inset 0 -2px 0 #FF6600`. Inactive: **13.5px / 400** `#6B7482`.
Hover: text `#070A4B`. The underline is the page's "you are here" — orange, 2px, nothing else
about the tab changes. Secondary/nested tab rows keep an azure `#2F5FD6` underline so only
one orange underline shows per screen.

### Segmented control
Height **32px**, radius 6px, 1px `#B8C4CE` border, `overflow: hidden`. Segments: padding
`0 14px`, divided by 1px `#B8C4CE` on the **inline-start** edge of each non-first segment.
Active segment: bg `#070A4B`, text `#fff` **12.5px / 800**. Inactive: text `#2E3450` 12.5px/400.

### Filter chips
Height **28px**, radius 999px, padding `0 12px`, **12.5px / 800**.
Applied: 1px `#2F5FD6`, bg `#EEF3FD`, text `#234BB0`, with a trailing `✕` in `#2F5FD6`.
Add: 1px **dashed** `#B8C4CE`, text `#6B7482` 12.5px/400, label `+ Add filter`.

### Side nav
A **230px overlay drawer** at every breakpoint (top-bar hamburger opens it; it closes on
navigate), not a permanent rail — the canvas always gets full width. Background `#FFFFFF`,
1px `#D7E1E8` border on the inline-end edge, `--e-el-3` shadow, scrim `rgba(7,10,75,.38)`
with a 3px blur. Padding 10px, item gap 2px. **Never fill the rail with pale blue** — white
chrome split by a hairline is the enterprise read.

Items: padding `8px 12px`, radius 6px, **13px**.
- Active: bg `#FFF4EC`, **3px `#FF6600` rail on the leading edge** (`box-shadow: inset 3px 0 0`
  under LTR, `inset -3px 0 0` under RTL — or `border-inline-start`), text `#070A4B` weight 800,
  icon `#FF6600`.
- Rest: text `#2E3450`, weight 400, icon `#6B7482`. Hover: bg `rgba(7,10,75,.05)`.
- Needs-attention marker: a **6px orange `#FF6600` dot** on the trailing edge.
- Divider: 1px `#E7EEF2`, margin `8px 12px`.
- Section under the divider (Settings, Admin): text `#6B7482`.

### Widget frame
A dashboard widget is a card with a header (`border-bottom`, padding `14px 20px`; title
15px/800 truncated, optional 12.5px muted subtitle, badge and actions on the trailing end) and
a flush body that owns its own padding. **Selected** is an azure `#2F5FD6` border plus
`box-shadow: inset 2px 0 0 #2F5FD6` on the leading edge \u2014 the same language a selected table
row speaks, and the sign flips under RTL. Never an orange glow: a coloured shadow on a data
screen reads as an error state, and selection here is a cursor, not a signal.

While selected, the widget floats a **toolbar** centred 12px above its own bottom edge:
`rgba(7,10,75,.92)` fill, radius 8px, `--e-el-2`, 4px padding, holding 28px icon buttons at
70% white \u2014 deselect, edit chart, filters, Ask Aidi.

### Context panel
The **only** right-hand aside in the product. Width 340px default (min 280, max 520), resizable
from a 1px handle on its inline-start edge, `border-inline-start: 1px #D7E1E8`, background
`#fff`. A 44px tab strip sits on top: underline tabs (icon + 12.5px/800 label, active carries
`inset 0 -2px 0 #2F5FD6`) with a close control at the trailing end. Sections register into it —
query specification, safety guarantees, lineage overview, asset metadata, the assistant —
rather than each page inventing its own panel. Below `lg` it is hidden entirely.

---

## Cards

Radius **8px**, 1px `#D7E1E8` border, **no shadow**, background `#fff`, padding **20px**.
Header eyebrow: **11px / 800 / +0.1em / uppercase** `#8A929F`.
KPI value: **32px / 800 / -0.02em** `#070A4B`, `font-variant-numeric: tabular-nums`.
The **lead KPI** in a row — the one measure the page exists to report — carries
`box-shadow: inset 3px 0 0 #FF6600` on its leading edge. One per row, never all of them.
Card-to-card gap: 16px. Section-to-section gap: 32px.
Put a `#FAFCFD` page fill behind white cards; white-on-white is what makes a screen read blank.

A card only gets `--e-el-1` if it is sticky or floating over scrolling content.

---

## Data table

**No zebra striping** — the hairline between rows does that job without adding a second value
to the page.

| Part | Spec |
| --- | --- |
| Toolbar | padding `14px 20px`, bottom border 1px `#D7E1E8`, bg `#fff`. Title 15px/800 `#070A4B`; sub-line 12.5px `#6B7482` tabular ("12,480 runs · updated 14:32 AST"). Actions right: 32px buttons. |
| Header row | height **38px**, bg `#DFEEF2`, bottom border 1px `#D7E1E8`. Cells **11px / 800 / +0.09em / uppercase** `#070A4B`, padding `0 20px`. |
| Body row | height **44px**. Bottom border 1px `#E7EEF2` (hairline). Last row: none. |
| Cell | padding `0 20px`, **13.5px**. Text columns `#2E3450`; the identifier column `#070A4B` weight 800 tabular. |
| Numeric cell | `text-align: right`, `font-variant-numeric: tabular-nums`. Emphasised measures `#070A4B`; secondary `#6B7482`. Out-of-band values take the status colour and weight 800. |
| Hover row | bg `#FAFCFD`. |
| Selected row | bg `#EEF3FD` **plus** `box-shadow: inset 2px 0 0 #2F5FD6` on the first cell — a rail on the **leading** edge, which flips to the right under RTL. |
| Footer | padding `12px 20px`, top border 1px `#D7E1E8`, bg `#FAFCFD`, **12.5px** `#6B7482` tabular. Range label on the leading edge ("Rows 1–4 of 12,480"), pager on the trailing edge. |
| Pager buttons | 28×28px, radius 6px, 1px `#B8C4CE`. Enabled `#2E3450`, disabled `#8A929F`. Chevrons flip under RTL. |

The pager range label must not be a shrinkable flex item — give it `flex: 0 0 auto` (or
`white-space: nowrap`), or it wraps to three lines in a narrow panel.

Column alignment: text leading-aligned, numbers trailing-aligned, status badges leading.
Put currency symbols and units in the **column header**, not the cell.

---

## Overlays

| Surface | Radius | Shadow | Notes |
| --- | --- | --- | --- |
| Dropdown / popover | 8px | `0 8px 24px rgba(7,10,75,.10)` | 1px `#D7E1E8` border, 4px padding, items 32px. |
| Tooltip | 6px | `0 8px 24px rgba(7,10,75,.10)` | bg `#070A4B`, text `#fff` 12.5px, padding `6px 10px`. |
| Dialog | 12px | `0 24px 60px rgba(7,10,75,.16)` | padding 24px, scrim `rgba(7,10,75,.32)`. |
| Side sheet | 12px on the inner edge | `0 24px 60px rgba(7,10,75,.16)` | Enters from the **inline-end** edge. |

Dialog footer: actions on the **inline-end** edge, secondary then primary, 8px gap.

---

## Motion

`cubic-bezier(.2,.8,.2,1)`. 120ms for state changes (hover, focus), 200ms for reveals
(dropdown, tooltip), 340ms for surfaces (dialog, sheet). Fades and short rises only — no
bounce, no spring. Respect `prefers-reduced-motion: reduce` by dropping to opacity-only.
