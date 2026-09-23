---
name: aidi-enterprise
description: Design system for Aidi AI Enterprise — the governed, multi-tenant enterprise face of the Aidi brand. Use when building or modifying any Aidi Enterprise UI (Aidi Chat, BI Dashboard, admin/governance consoles) to get the correct colors, typography, spacing, component specs, dark theme, chart palette, and Arabic/RTL rules. Applies to enterprise product surfaces only, not the SMB POS product.
---

# Aidi AI Enterprise

The enterprise register of the Aidi brand. Same logo, same typefaces, same navy — different
discipline. Built for data analysts, LOB managers, government sector teams and IT admins who
sit in this product for seven hours a day.

**Do not use this system for the SMB POS product** (Aidi POS, till, kiosk, waiter app). That
product uses the consumer Aidi Design System: 20px radii, orange primary buttons, soft navy
shadows. Enterprise is the opposite of all three.

## Non-negotiables

Get these wrong and nothing else matters.

1. **Primary buttons are navy `#070A4B`, not orange.** Orange is a signal, not a default.
2. **Every screen carries orange — 2 to 4 small accents, never zero.** A screen with no
   orange is an unbranded screen. See "The orange rule" below for the sanctioned slots.
3. **Side nav and app chrome are white `#FFFFFF`**, separated by a 1px `#D7E1E8` border —
   not a pale-blue fill. Pale blue is for table headers and hover fills only.
3. **Control height is 36px. Table row height is 44px.** Everywhere.
4. **Radius is 6px for controls, 8px for cards.** Never 12px on a card, never 20px on anything.
5. **Elevation 0 is the default.** A card gets a 1px border, not a shadow. Shadows are only
   for things literally floating over other things.
6. **Every numeric cell is `font-variant-numeric: tabular-nums`.** Tables, KPIs, timestamps, IDs.
7. **Focus ring is 2px azure `#2F5FD6` at 2px offset.** Never orange.
8. **RTL is not a late variant.** Build every screen with logical properties from the start.

## The orange rule

`#FF6600` is the brand's most recognisable asset. On a white enterprise surface it is also
the only thing that says *Aidi* rather than *generic admin console*. It is rationed, not
banned: **orange marks where you are and what matters.**

Every screen should land **2 to 4 orange touchpoints**, all small and structural. Pick from
this list — do not invent new ones:

| Sanctioned orange | Never orange |
| --- | --- |
| **Active side-nav item** — 3px rail on the leading edge + `#FFF4EC` fill, navy label | Primary buttons (navy) |
| **Active tab underline** — 2px `#FF6600` | Focus rings (azure `#2F5FD6`) |
| **Lead KPI card** — 3px inset rail on the leading edge (one card per row) | Links (azure) |
| **The emphasised / forecast chart series** | Any categorical chart hue |
| One `Signal` button, for the action the page exists to produce | Large fills, page or card backgrounds |
| 6px attention dot · starred item · `NEW` tag · brand mark | Body text, table text, card borders |

Proportion is the rule, not count: orange should be **under 2% of the pixels** on any screen.
Rails, dots, 2px underlines and one small logo mark — never a panel, never a button row.

When a screen still reads flat after this, the fix is **hierarchy, not more orange**: raise the
KPI value to 32px/800 navy, drop labels to `#8A929F` micro-caps, and put a `#FAFCFD` fill
behind the page so white cards separate from it.

## Color

Full scales in `references/tokens.md`. Drop-in CSS custom properties in
`assets/aidi-enterprise.css` (light + dark, plus a `data-theme="dark"` block).

The working set:

```
Ink        #070A4B  primary text, primary buttons, active nav, chart series 1
Azure      #2F5FD6  interactivity — links, focus, selection
Orange     #FF6600  signal + active state (see above)
Chrome     #FFFFFF  side nav, top bar, cards — white, split by 1px #D7E1E8
Pale blue  #DFEEF2  table header rows and hover fills only
Signal tint #FFF4EC active nav item fill, behind an orange rail
Border     #D7E1E8  default 1px border · #E7EEF2 hairline · #B8C4CE strong/control
Text       #070A4B strong · #2E3450 body · #6B7482 muted · #8A929F subtle
```

Status colors are darkened until they clear 4.5:1 as text on white. Tints are fill-only and
never carry text at small sizes:

```
Success  #0F7A52 on #E4F2EC      Warning  #B25A00 on #FBEBDD
Danger   #B3261E on #F9E6E4      Info     #234BB0 on #E7EDFA
```

Dark theme is navy taken all the way down, never grey. Azure lifts two steps to `#7D9DEA` and
orange lifts to `#FF8433` to hold contrast on dark ink.

```
page #060820 · card #0C1033 · raised #141A45 · border #262C5E
text #F2F5FB strong · #C7CDE4 body · #8D95B8 muted
```

## Typography

Anodina (Latin) and Manifa V2 (Arabic). Both ship with this skill in `assets/fonts/`.

Anodina has **three weights only** — Light 300, Regular 400, ExtraBold 800. There is no
medium or semibold; do not synthesise one. Enterprise uses 400 and 800 almost exclusively —
Light 300 is display-size only, never body copy at 15px.

Manifa V2 has **two weights** — Regular 400 and Bold 700. Bold covers everything Anodina uses
800 for.

```
Display   30px / 1.2  / 800 / -0.015em
Title     20px / 1.25 / 800 / -0.01em
Section   13px / 1.3  / 800 / +0.1em  uppercase
Body      15px / 1.55 / 400 / 0
Small     13.5px / 1.5 / 400
Micro     11px / 1.3 / 800 / +0.12em  uppercase — eyebrows, table headers
```

Base body is 15px/1.55, `#2E3450`. Buttons and table numerics are 13.5px/800.

## Spacing & shape

4px grid, inherited unchanged. Comfortable, not console-tight.

```
Control height  36px      Table row     44px (also the minimum touch target)
Card padding    20px      Section gap   32px in product
Radius   0 tables/dividers · 4 checkboxes/tags · 6 controls · 8 cards (default)
         12 dialogs · 999px status pills and filter chips only
Elevation  0 flat (border, default) · 1 raised 0 1px 2px rgba(7,10,75,.06)
           2 overlay 0 8px 24px rgba(7,10,75,.10) · 3 modal 0 24px 60px rgba(7,10,75,.16)
```

The orange glow shadow from the SMB system is retired. Colored shadows on a data screen read
as an error state.

## Components

Exact specs — heights, colors, states, per component — are in `references/components.md`.
Read it before implementing any of: buttons, fields, status badges, navigation, data tables.

The short version:

- **Buttons** 36px, radius 6, 13.5px/800. Primary navy → hover `#14184F` → active `#04062F`.
  Secondary is white with a `#B8C4CE` border. Tertiary is bare azure text.
- **Fields** 36px, radius 6, `#B8C4CE` border, 12px inline padding. Focus is a `#2F5FD6`
  border plus `0 0 0 2px rgba(47,95,214,.22)`.
- **Status badges** are the one surviving use of pills. Each status carries a distinct **glyph
  shape** as well as a color — dot, diamond, bar, ring — so the set survives greyscale and
  color-vision deficiency.
- **Tables** have no zebra striping. Header row sits on `#DFEEF2`. Rows are separated by a
  `#E7EEF2` hairline. Selection is an azure-50 tint plus a 2px inset rail on the **leading**
  edge (which flips under RTL). Numbers right-align, tabular.
- **Side nav** is an **overlay drawer**, 230px, opened from the top-bar hamburger and closed
  on navigate — the canvas keeps full width at every breakpoint. It is **white** with a 1px
  `#D7E1E8` inline-end border — chrome reads by its border, not by a tint. The active item is
  a `#FFF4EC` fill with a **3px `#FF6600` rail on the leading edge** (it flips under RTL) and
  a navy 800 label. Rest state is `#2E3450` 400; hover is `rgba(7,10,75,.05)`.
- **Context panel** is the only right-hand aside in the product: 340px default, resizable,
  `border-inline-start`, a 44px tab strip with underline tabs, and a close control. Anything
  that wants to live beside a page (spec, safety, filters, the assistant) registers as a
  section here rather than rendering its own panel.

## Arabic & RTL

The brand is Arabic-first, roughly 70/30 in practice. `references/rtl-arabic.md` has the full
rules. The ones that bite:

- **Never letter-space Arabic.** Anodina's −1.5% display tracking must be zeroed for Manifa.
- **Set `size-adjust: 130%` on both Manifa `@font-face` rules** (already in
  `assets/aidi-enterprise.css`). Without it one `font-size` renders Arabic about a third
  smaller than Latin. Do not also step Arabic sizes up per element — the two stack. Loosen
  leading though (body 1.55 → 1.7): Manifa's ascenders reach further.
- **Numerals stay Western-Arabic (0-9) in Anodina, tabular, in both directions.** Manifa V2
  maps the Arabic-Indic codepoints to Western letterforms, so Arabic-Indic numerals are not
  available. This also keeps figures matching the SQL, the CSV export and the spreadsheet.
- **Currency and units flip position**: `﷼ 1,240` in English, `1,240 ﷼` in Arabic. Put the
  symbol in the column header, not the cell, wherever you can.
- **Wrap every Latin identifier** (SKU, email, VAT number, batch ID) in `dir="ltr"` inside
  Arabic text.
- **Do not mirror**: chart time axes (oldest stays left — the axis is numeric), the logo
  lockup, non-directional icons (clock, check, download, search, filter), media transport
  controls, anything reading as a timeline.

Use CSS logical properties throughout — `padding-inline-start`, `margin-inline-end`,
`inset-inline-start`, `border-inline-start` — not `left`/`right`. Then `dir="rtl"` on the
document root is the entire RTL implementation.

## Data visualisation

Eight categorical hues walking navy → azure → steel → sky → lavender → slate. The ramp is
deliberately close in hue so that orange, held out of the set, reads instantly as the
emphasised series.

```
01 #070A4B  02 #2F5FD6  03 #5B93C9  04 #8FB9CE
05 #8E86B8  06 #B2AAC4  07 #6B7482  08 #B8C4CE
highlight #FF6600 — assigned to what the user selected, never to a category
```

Sequential (one measure, low→high): `#EDF3F8 → #B9D2E6 → #7FA9D2 → #4A7BC0 → #2450A0 → #070A4B`
Diverging (variance around a target): `#B25A00 → #E8A66A → #F2F6F8 → #8FB9CE → #2450A0`

More in `references/dataviz.md`.

## What's in this skill

```
assets/aidi-enterprise.css       drop-in CSS custom properties, light + dark + @font-face
assets/tailwind.aidi.js          Tailwind theme extension with the same tokens
assets/fonts/                    Anodina ×3, Manifa V2 ×2
assets/logo/                     SVG lockups, wordmarks, marks (navy/white/orange)
references/tokens.md             every token, full scales, with usage
references/components.md         exact per-component specs and states
references/rtl-arabic.md         bilingual and RTL implementation rules
references/dataviz.md            chart palette, axis, tooltip and legend rules
reference-designs/*.html         the five HTML design references
MIGRATION-PROMPT.md              paste-ready prompt for re-skinning an existing frontend
```

`reference-designs/` are **design references, not production code** — HTML prototypes showing
intended look and behaviour. Recreate them in the target codebase's own framework and
patterns. Read them to resolve any question this document does not answer:

- `guidelines.html` — the full design system document, with live specimens
- `login.html` — sign-in, English (LTR) and Arabic (RTL), light and dark
- `aidi-chat.html` — the conversational data assistant
- `bi-dashboard.html` — the analytics dashboard
- `page-templates.html` — **twelve product surfaces in one shell**, with a live language (EN/AR),
  theme (light/dark) and page switcher: Ask Aidi (full-page empty state), Data Explorer,
  Dashboards library, Reports + detail, Alerts, Deliveries, Agent runs + run detail, Data
  Catalog, Semantic models, Lineage, Documents, Settings. Built from the real
  `frontend/src/` source — the shell is the overlay nav drawer from `layouts/AppShell.tsx`,
  the right-hand panel is `components/shell/ContextPanel.tsx` (340px, underline tab strip),
  and each page follows its own `pages/*.tsx` composition.

## Icons

Lucide, 2px rounded stroke, rendered in `currentColor` — navy by default, azure when active.
Standard sizes 15px inline, 16px in controls, 20px in nav. This is a documented substitution;
the brand ships no official UI icon set.

## Voice

Conversational, friendly, formal — but the enterprise register is drier than the SMB one.
Speak to the user as "you", about the product as "we". No exclamation marks in product chrome.
No emoji in enterprise product UI (the approved emoji set is social/community contexts only).
Section headers and eyebrows are ALL-CAPS with `+0.1em` tracking; everything else is sentence
case. State what happened and what it costs; do not congratulate the user for running a query.
