# Aidi Enterprise — Tokens

Machine-readable equivalents: `assets/aidi-enterprise.css` (CSS custom properties),
`assets/tailwind.aidi.js` (Tailwind theme extension).

---

## Brand anchors — unchanged

These six are the values on the Aidi Logo Card. They carry over verbatim; what changes is the
job each one does.

| Hex | Name | SMB job | Enterprise job |
| --- | --- | --- | --- |
| `#FF6600` | Orange | Primary action — every CTA, link, focus ring, active tab | **Signal only.** Brand marks, one capped CTA per screen, the emphasised chart series |
| `#070A4B` | Navy | Ink and inverse panels, secondary buttons | **Ink and primary action.** Headings, primary buttons, active nav, chart series 1 |
| `#B2AAC4` | Lavender | Illustration volumes | Categorical chart hue 6 |
| `#8A929F` | Slate | Secondary text, dividers | Subtle text, chart hue 7 |
| `#DFEEF2` | Pale blue | Section washes, cards | **Table header rows and hover fills only** — never the side nav, which is white |
| `#221F20` | Charcoal | Max-contrast body text | Rare. Body text is `#2E3450` |

Azure `#2F5FD6` is the one genuinely new hue. In the SMB system it existed only as
`--info: #3A6DE0`. Enterprise promotes it to carry **interactivity** — links, focus rings,
selection, active tab underlines — the job orange used to do.

---

## Full scales

### Ink (navy)
| Token | Hex | Use |
| --- | --- | --- |
| `--e-ink-950` | `#04062F` | Primary button active |
| `--e-ink-900` | `#070A4B` | Brand navy. Primary text, primary button, chart 1 |
| `--e-ink-800` | `#14184F` | Primary button hover |
| `--e-ink-700` | `#262B6B` | Dark-panel gradients |
| `--e-ink-600` | `#3B4189` | |
| `--e-ink-500` | `#565CA6` | |

### Azure (interactivity)
| Token | Hex | Use |
| --- | --- | --- |
| `--e-azure-50` | `#EEF3FD` | Selected row tint, applied-chip fill |
| `--e-azure-100` | `#D9E4FA` | Tertiary button active |
| `--e-azure-200` | `#B3C8F4` | Dark-header eyebrows |
| `--e-azure-300` | `#7D9DEA` | **Dark-theme azure** |
| `--e-azure-400` | `#5482E5` | |
| `--e-azure-500` | `#2F5FD6` | Focus ring, active tab, selection rail, chart 2 |
| `--e-azure-600` | `#234BB0` | Links, tertiary button text, info |
| `--e-azure-700` | `#1A3A8A` | Link hover |

### Orange — signal only
`--e-orange-100 #FFE0CC` · `--e-orange-500 #FF6600` · `--e-orange-600 #E65B00` ·
dark theme `#FF8433`

### Neutrals
`0 #FFFFFF` · `25 #FAFCFD` · `50 #F2F6F8` · `100 #E7EEF2` · `150 #DFEEF2` · `200 #D7E1E8` ·
`300 #B8C4CE` · `400 #8A929F` · `500 #6B7482` · `600 #515966` · `700 #3B4250` · `900 #221F20`

### Semantic
```
text-strong   #070A4B      border         #D7E1E8
text-body     #2E3450      border-strong  #B8C4CE   (control outlines)
text-muted    #6B7482      border-hair    #E7EEF2   (table row separator)
text-subtle   #8A929F      focus          #2F5FD6
```

### Status
All four are darkened until they clear **4.5:1 as text on white**. The tints are fill-only and
never carry text at small sizes.

| | Text | Tint |
| --- | --- | --- |
| Success | `#0F7A52` | `#E4F2EC` |
| Warning | `#B25A00` | `#FBEBDD` |
| Danger | `#B3261E` | `#F9E6E4` |
| Info | `#234BB0` | `#E7EDFA` |

---

## Dark theme

Navy taken all the way down, never grey. Azure lifts two steps and orange lifts one to hold
contrast against dark ink.

```
page    #060820      text-strong  #F2F5FB
card    #0C1033      text-body    #C7CDE4
raised  #141A45      text-muted   #8D95B8
border  #262C5E      azure        #7D9DEA
                     signal       #FF8433
```

Primary button on dark is **azure `#2F5FD6`**, not navy — navy vanishes into the page.
Secondary is transparent with a `#3A4179` border and `#C7CDE4` text.

---

## Typography

| Role | Size | Leading | Weight | Tracking |
| --- | --- | --- | --- | --- |
| Display | 30px | 1.2 | 800 | −0.015em |
| Page title | 20px | 1.25 | 800 | −0.01em |
| Section header | 13px | 1.3 | 800 | +0.1em, uppercase |
| Lead paragraph | 16px | 1.55 | 400 | 0 |
| Body | 15px | 1.55 | 400 | 0 |
| Small / table | 13.5px | 1.5 | 400 | 0 |
| Micro / eyebrow | 11px | 1.3 | 800 | +0.12em, uppercase |
| KPI value | 32px | 1.1 | 800 | −0.02em, tabular |

**Anodina** ships Light 300, Regular 400, ExtraBold 800 — no medium, no semibold, do not
synthesise one. Enterprise uses 400 and 800 almost exclusively; Light 300 is display-size only
and never body copy.

**Manifa V2** ships Regular 400 and Bold 700. Bold covers everything Anodina uses 800 for.
There is no Light equivalent, which is why Arabic display sizes step up rather than thin out.

Numerals are **always tabular** in tables, KPIs, timestamps and IDs:
`font-variant-numeric: tabular-nums`.

---

## Spacing & shape

4px grid, inherited unchanged from the SMB system. The target is a screen someone can read for
seven hours, not one that fits the most rows.

| Value | Meaning | Was (SMB) |
| --- | --- | --- |
| 36px | Control height — buttons, inputs, selects | 44px |
| 44px | Table row height, and the minimum touch target | — |
| 20px | Card padding | 24–32px |
| 32px | Section rhythm in-product | 64px |

### Radius
`0` table cells, dividers, full-bleed bars · `4` checkboxes, swatches, tags ·
`6` buttons, inputs, selects, menus · `8` cards, panels, charts (default) ·
`12` dialogs, sheets, popovers · `999px` status badges and filter chips **only**

### Elevation
| Level | Value | Use |
| --- | --- | --- |
| 0 · Flat | 1px `#D7E1E8` border, no shadow | Cards, panels, tables. **The default.** |
| 1 · Raised | `0 1px 2px rgba(7,10,75,.06)` | Sticky headers, pinned columns, toolbars over scroll |
| 2 · Overlay | `0 8px 24px rgba(7,10,75,.10)` | Dropdowns, popovers, tooltips, autocomplete |
| 3 · Modal | `0 24px 60px rgba(7,10,75,.16)` | Dialogs and side sheets, over a scrim |

If a surface is not literally on top of something, it gets a border instead of a shadow.
The orange glow shadow is retired in product — coloured shadows on a data screen read as an
error state at a glance.

---

## Migration from the consumer Aidi Design System

Component APIs are untouched. Every change is a value swap in `tokens/`, which means an app
picks up the enterprise register by swapping the token file — no component rewrites.

| Token | SMB | Enterprise |
| --- | --- | --- |
| `--action-primary` | `#FF6600` | `#070A4B` |
| `--shadow-focus` | 3px orange | 2px `#2F5FD6` at 2px offset |
| `--radius-card` | 20px | 8px |
| `--radius-control` | 12px | 6px |
| `--control-height` | 44px | 36px |
| `--card-padding` | 24–32px | 20px |
| `--section-gap` | 64px | 32px |
| `--shadow-card` | soft navy ambient | none (1px border) |
| `--shadow-orange` | warm glow | retired |
| `--info` | `#3A6DE0` | promoted to the azure scale |
