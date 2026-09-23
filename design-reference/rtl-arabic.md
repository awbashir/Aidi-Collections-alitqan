# Aidi Enterprise — Arabic & RTL

The brand is Arabic-first, roughly 70% Arabic / 30% English in practice. RTL is not a variant
that ships late; every screen is built for both directions from the start.

---

## Implementation

Use **CSS logical properties** everywhere. Then `dir="rtl" lang="ar"` on `<html>` is the entire
RTL implementation, with no per-component overrides.

| Use | Not |
| --- | --- |
| `padding-inline-start` / `-end` | `padding-left` / `right` |
| `margin-inline-start` / `-end` | `margin-left` / `right` |
| `inset-inline-start` / `-end` | `left` / `right` |
| `border-inline-start` | `border-left` |
| `text-align: start` / `end` | `text-align: left` / `right` |
| `justify-content: flex-start` in a flex row | any hard-coded direction |

`box-shadow: inset 2px 0 0` (the table selection rail) does **not** flip automatically — swap
the sign under RTL, or use a `border-inline-start` instead.

### Headings will not inherit the Arabic face

Setting `font-family` and `letter-spacing: 0` on an Arabic wrapper is not enough. Base
stylesheets almost always carry a rule like `h1,h2,h3 { font-family: var(--font-display);
letter-spacing: var(--tracking-tight) }` — a cascaded rule beats inheritance, so every heading
inside the wrapper keeps the Latin face and the Latin negative tracking. Anodina has no Arabic
coverage, so those headings fall back per-glyph to an arbitrary system face **and** get display
tracking applied to cursive joins: the two things this system most forbids, on the largest type
on the page.

Always ship a defensive rule that outranks the base:

```css
[lang="ar"] h1, [lang="ar"] h2, [lang="ar"] h3, [lang="ar"] h4 {
  font-family: "Manifa V2", "Noto Sans Arabic", sans-serif;
  letter-spacing: 0;
}
```

Verify by reading **computed** styles on a heading, not the wrapper. If `font-family` comes back
as the Latin face on an Arabic string, the bug is present.

Icons: flip directional ones (chevrons, arrows, back, next, indent, pager controls). Do not
flip the rest — see below.

---

## Type

| Rule | Why |
| --- | --- |
| **Never letter-space Arabic.** Set `letter-spacing: 0` on every Arabic run. | Tracking severs the cursive joins and turns a word into loose glyphs. Anodina's −1.5% display tracking must be zeroed for Manifa. |
| **`size-adjust: 130%` on both Manifa `@font-face` rules — and no per-element step-up on top of it.** | Manifa draws its glyphs far smaller on the em square (cap height 0.470em against Anodina's 0.700em), so one `font-size` renders two different optical sizes. `size-adjust` scales outlines and metrics together, so one number means one size in both languages and no component has to know which language it is in. 130% takes Manifa to 0.611em — a deliberate step short of full parity (~150%), because at parity Arabic body copy overpowers the Latin around it on mixed screens. Same value on Regular and Bold: a per-weight number makes a line visibly change size the moment it bolds. **Do not also add +1px to Arabic sizes** — that compensation predates `size-adjust` and the two stack. |
| **Loosen leading** (body 1.55 → 1.7; display 1.15 → 1.28) even after size-adjust. | Manifa's ascenders and descenders reach further than Anodina's. |
| **Two weights, not three.** Manifa V2 is Regular 400 and Bold 700. Map Anodina 800 → Manifa 700, Anodina 400 → Manifa 400. | There is no Light equivalent, which is why Arabic display sizes step up rather than thin out. |
| **Mixed labels sit side by side**, separated by a 1px rule — not stacked. | Keeps the bilingual pairing legible in a KPI eyebrow or a column header. |

Matched display pair, for reference:
- Latin: Anodina ExtraBold 800, 30px, tracking −1.8%, leading 1.15
- Arabic: Manifa V2 Bold 700, 31px, tracking 0, leading 1.28

---

## Numerals

**Numerals stay Western-Arabic (0–9), set in Anodina, tabular, in both directions.**

Two reasons:
1. Manifa V2 maps the Arabic-Indic codepoints (٠١٢٣٤) to Western letterforms — the two runs
   render pixel-identically, so Arabic-Indic numerals are not actually available in this face.
2. Western-Arabic matches the SQL the answer ran, the CSV export, and the figures analysts
   paste into a spreadsheet. Changing them per-locale breaks that chain.

```css
.num { font-family: "Anodina", sans-serif; font-variant-numeric: tabular-nums; }
```

### Currency and units
The riyal glyph **leads in English and trails in Arabic**:

```
en:  ﷼ 184,203.50            ar:  184,203.50 ﷼
en:  12.4%                   ar:  ٪12.4  → prefer putting % in the column header
```

Percentages and units follow the same rule, so **put the symbol in the column header** rather
than in every cell wherever the table allows it. That removes the flip problem entirely.

### Latin identifiers inside Arabic
Wrap every one in `dir="ltr"` — batch numbers, SKUs, VAT registrations, emails, tenant IDs,
run IDs, hostnames. Without it the bidi algorithm reorders the segments.

```html
<span dir="ltr" style="font-family:'Anodina',sans-serif">TEN-4F82-9C10-B3D7</span>
```

---

## Do not mirror

| Element | Why |
| --- | --- |
| The time axis on a chart | Oldest stays left — the axis is numeric, not linguistic |
| The logo lockup and the `أ` monogram | Fixed artwork |
| Non-directional icons: clock, check, download, search, filter, settings | Nothing about them points |
| Media transport controls | Play always points at the direction of playback |
| Anything reading as a timeline | Progress bars, step indicators tied to time, Gantt rows |
| Code, SQL, file paths, URLs | Always LTR |

Everything else mirrors: layout, nav rails, table column order, chevrons, pager arrows,
side-sheet entry edge, selection rails, drop shadows with a directional offset.

---

## Parity checklist

Run this on every screen before it ships. Control heights, row heights, column count and the
orange budget must be **identical** in both directions.

- [ ] Document `dir` and `lang` set; no hard-coded `left`/`right` in the stylesheet
- [ ] Arabic font swapped and `letter-spacing: 0` applied to all Arabic runs
- [ ] Arabic sizes stepped up ~1px below 18px; leading loosened
- [ ] All numerals Western-Arabic, Anodina, tabular, in both directions
- [ ] Currency/unit position flipped, or moved into the column header
- [ ] Every Latin identifier wrapped in `dir="ltr"`
- [ ] Table selection rail flipped to the leading edge
- [ ] Pager chevrons flipped; chart time axis **not** flipped
- [ ] Side sheet enters from the inline-end edge
- [ ] Text does not overflow: Arabic runs 15–25% longer than English at the same size —
      no fixed-width labels, no `white-space: nowrap` on translated copy
- [ ] Truncation uses `text-overflow: ellipsis` with a logical `max-inline-size`

---

## Copy

Arabic uses **لهجة بيضاء** — "white dialect": neutral, pan-Gulf, MSA-leaning, no local slang.
Same register as the English: conversational, friendly, formal. Speak to the user as "you"
(أنت), about the product as "we" (نحن).

Do not machine-translate product chrome. Column headers, status labels and error messages are
the copy most likely to be wrong and most expensive to get wrong.
