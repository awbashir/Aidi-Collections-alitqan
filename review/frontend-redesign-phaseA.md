# CAWP frontend → Aidi AI Enterprise — Phase A

**Scope:** design tokens, self-hosted fonts, UI primitives, app shell (side nav + header),
login, Tailwind safelist. Page views (`src/views/**`, `src/components/shared/**`, dashboards)
are deliberately untouched — that is Phase B.

**Status:** `npm run build` passes. `npx tsc --noEmit` = **5,315 lines before, 5,315 after**
(zero new type errors). No console errors and no failed requests on sign-in / home / users /
clients. All five self-hosted faces load (`Anodina 400/800`, `Manifa V2 400/700`; `Anodina 300`
stays unloaded because nothing requests weight 300 — correct, Light is display-only).

---

## 1. Headline numbers

| | Before | After |
| --- | --- | --- |
| `build/assets/index-*.css` | **556,918 B** (556 kB) | **263,834 B** (264 kB) — **−53 %**, gzip 29.8 kB |
| `safelist.txt` | 4,327 classes / 87.9 kB | **1,231 classes / ~25 kB** — **−72 %** |
| `build/` total | 8.1 MB | 8.0 MB (fonts +1.1 MB in, Google Fonts CDN out) |
| Network font requests at runtime | 2 × Google Fonts CSS + faces | 0 (all self-hosted, bundled) |
| `tsc` errors | 5,315 | 5,315 |

---

## 2. Token mapping — template class → Aidi value

### Colour scales (`tailwind.config.cjs` → `theme.extend.colors`)

`themeColor` is now **`navy`** and `primaryColorLevel` stays **600**, so the template's
runtime `${themeColor}-${primaryColorLevel}` machinery (86 `useThemeClass` consumers +
~27 primitives) lands on the spec without touching a single call site.

| Class | Hex | Spec role |
| --- | --- | --- |
| `navy-50` | `#EEF3FD` | azure-50 · selected-row tint, tertiary hover |
| `navy-100` | `#D9E4FA` | tertiary active |
| `navy-200` | `#B3C8F4` | dark-header eyebrow |
| `navy-300` | `#7D9DEA` | **dark-theme azure** |
| `navy-400` | `#5482E5` | |
| `navy-500` | `#2F5FD6` | **azure** — focus ring, selection rail, active tab logic |
| **`navy-600`** | **`#070A4B`** | **primary rest** — buttons, ink, active nav label, chart 1 |
| `navy-700` | `#14184F` | primary hover |
| `navy-800` / `900` | `#04062F` | primary active |
| `indigo-*` | = `navy-*` | the template defaults to indigo in a few spots |

| Class | Hex | Spec role |
| --- | --- | --- |
| `gray-50` | `#FAFCFD` | page fill behind white cards |
| `gray-100` | `#F2F6F8` | disabled fill, secondary-button hover |
| `gray-200` | `#E7EEF2` | hairline / row separator |
| `gray-300` | `#D7E1E8` | **default 1px border** (cards, chrome, dividers) |
| `gray-400` | `#B8C4CE` | **strong border** — control outlines |
| `gray-500` | `#8A929F` | subtle text, eyebrows |
| `gray-600` | `#6B7482` | muted text |
| `gray-700` | `#2E3450` | **body text** |
| `gray-800` | `#0C1033` | dark card / chrome |
| `gray-900` / `950` | `#060820` / `#030413` | dark page |

Also added: `ink`, `azure`, `signal` (+ `50 #FFF4EC`, `700 #CC5200` added by me),
`slate` (incl. `slate-150 #DFEEF2` = table header), `lavender`, `success/warning/danger/info`
(`DEFAULT` + `tint`), `viz-1..8` + `viz-highlight`, `night-{page,card,raised,border,border-strong,strong,body,muted}`.
`red` was **overridden** to the Aidi destructive ramp (`red-500/600 #B3261E`, `700 #8F1E17`,
`800 #6E1712`, `100 #F9E6E4`) because the template hard-codes `red-500` for invalid fields,
`form-explain` and `badge`, and 31 call sites pass `color="red-600"` to `Button`.

### Shape, elevation, type, motion

| Template class | Before | After (Aidi) |
| --- | --- | --- |
| `rounded` (DEFAULT) / `rounded-md` | 4 / 6 px | **6 px** — buttons, inputs, selects, menus |
| `rounded-sm` / new `rounded-xs` | 2 px | **4 px** — checkboxes, tags, swatches |
| `rounded-lg` | 8 px | **8 px** — cards, panels, dropdowns |
| `rounded-xl` / `rounded-2xl` | 12 / 16 px | **12 px** — dialogs, sheets |
| new `rounded-pill` | — | **999 px** — status badges, filter chips only |
| `shadow-sm` / `shadow` | grey ambient | `0 1px 2px rgba(7,10,75,.06)` (el-1) |
| `shadow-md` / `shadow-lg` | grey ambient | `0 8px 24px rgba(7,10,75,.10)` (el-2) |
| `shadow-xl` / `shadow-2xl` | grey ambient | `0 24px 60px rgba(7,10,75,.16)` (el-3) |
| new `shadow-{raised,overlay,modal,focus,field}` | — | the four elevations + the two rings |
| `font-sans` | Cairo, Inter (Google CDN) | **`Anodina, "Manifa V2", "Noto Sans Arabic", "Segoe UI", system-ui`** |
| `h-9` / `h-8` / `h-7` / `h-10` | — | 36 / 32 / 28 / 40 px, wired through `CONTROL_SIZES` |
| new `h-control`, `h-row`, `h-row-head`, `p-card`, `gap-section` | — | 36 / 44 / 38 / 20 / 32 px |
| `duration-fast` / `duration` / `duration-slow` | 150 ms | **120 / 200 / 340 ms** |
| `ease-aidi` | — | `cubic-bezier(.2,.8,.2,1)` |
| new `text-{micro,section,small,body,lead,title,display,kpi}` | — | the enterprise type scale |

`CONTROL_SIZES` changed `md: 11 → 9` (44 → **36 px**) and `sm: 9 → 8` (36 → **32 px**),
`lg: 14 → 10`, which re-heights Button, Input, Select, Segment, InputGroup addons and
FormItem labels at once — no call sites touched.

---

## 3. Files changed

### Config / entry
- `tailwind.config.cjs` — rewritten. Merges `src/assets/aidi/tailwind.aidi.cjs`, adds the
  `navy` / `gray` / `indigo` / `red` overrides, remaps radius + shadow, and replaces the
  blanket safelist with an explicit pattern list (see §5).
- `src/index.css` — imports `assets/aidi/aidi-enterprise.css` **first**.
- `src/assets/styles/app.css` — the two Google Fonts `@import`s removed.
- `src/assets/styles/tailwind/index.css` — `@layer base` rewritten: body 15/1.55 `#2E3450`
  on `#FAFCFD`, headings retuned to the 30/24/20/17/15/13.5 scale at weight 800 in navy,
  `[dir="rtl"] { letter-spacing: 0 }`, plus `.text-num` / `.text-link` / `.text-eyebrow` utilities.
- `src/assets/aidi/aidi-enterprise.css` — three surgical edits (see §6).
- `index.html` — `lang="ar" dir="rtl"`, title `Aidi Collections — فنية الاتقان`, Arabic meta
  description, favicon → `/aidi/aidi-mark-navy.svg`, **font-awesome CDN `<link>` removed**
  (`grep "fa fa-"` over `src/` → 0 survivors).
- `src/configs/theme.config.ts` — `themeColor: "lime" → "navy"`.
- `src/components/ui/ConfigProvider/ConfigProvider.ts` — default `themeColor: 'indigo' → 'navy'`.
  (`themeSlice.ts` derives from `themeConfig`, so no duplicate to change.)
- `src/constants/theme.constant.ts` — `SIDE_NAV_WIDTH 290 → 230`, collapsed `80 → 64`,
  `HEADER_HEIGHT_CLASS h-16 → h-14`, `SIDE_NAV_CONTENT_GUTTER px-4 → px-2.5`, `LOGO_X_GUTTER px-6 → px-4`.
- `src/constants/app.constant.ts` — `APP_NAME = "Aidi Collections"`, new `TENANT_NAME = "فنية الاتقان"`.
- `public/aidi/` — added `aidi-mark-orange.svg`, `aidi-mark-white.svg`, `aidi-wordmark-{navy,white}.svg`.

### Component stylesheets (`src/assets/styles/components/`)
Rewritten: `_button`, `_input`, `_card`, `_tabs`, `_tag`, `_badge`, `_pagination`, `_tables`,
`_tooltip`, `_dropdown`, `_menu`, `_menu-item`, `_dialog`, `_drawer`, `_checkbox`, `_radio`,
`_switcher`, `_select`, `_notification`, `_form`, `_segment`, `_progress`, `_steps`, `_avatar`,
`_date-picker`. Touched: `_close-button`, `_upload`, `_skeleton`, `_timeline`.
Template: `_header`, `_side-nav`, `_secondary-header`. Vendors: dark-surface class sweep only.

Highlights — card 8 px / 1px `#D7E1E8` / **no shadow** / 20 px padding (`.card-shadow` now
renders a border too); table header 38 px on `#DFEEF2` with 11 px/800/+0.09em navy cells
(tracking zeroed under RTL), body rows 44 px, `#E7EEF2` hairlines, **no zebra**, hover `#FAFCFD`,
`font-variant-numeric: tabular-nums` on every `td`/`th`, `.table-row-selected` = `#EEF3FD`
+ a 2 px azure inset rail that flips under RTL; tabs active = 13.5/800 navy with
`inset 0 -2px 0 #FF6600`; pagination pagers 28 px / radius 6 / 1px `#B8C4CE`; dropdown 8 px
+ el-2, dialog 12 px + el-3, tooltip navy 6 px; status-pill classes `.tag-{success,warning,danger,info,neutral}`
+ `.tag-meta` added for Phase B to consume.

### UI primitives (`src/components/ui/`)
- `utils/constants.ts` — `CONTROL_SIZES`.
- `Button/Button.tsx` — local `shiftLevel()` replaces `useColorLevel` so hover/active step
  **up** the scale (navy 600 → 700 → 800; `red-600` → 700 → 800 = the spec's destructive ramp);
  secondary = white + `gray-400` border + navy label; tertiary = azure text on a `navy-50` hover
  fill; **`color="signal"` defaults to level 500** so `variant="solid" color="signal"` gives
  `#FF6600 → #E65B00 → #CC5200` with no API change; per-size padding retuned to 18/14/12 px.
- `Input/Input.tsx` — focus ring hard-pinned to **azure**, no longer `${themeColor}`.
- `Select/Select.tsx` — all `twColor[themeColor][...]` lookups replaced with fixed Aidi hexes
  (rest `#B8C4CE`, focus `#2F5FD6` + `0 0 0 2px rgba(47,95,214,.22)`, invalid `#B3261E`),
  radius 6, height from `CONTROL_SIZES`.
- `Tabs/TabNav.tsx` — stopped building `border-${themeColor}-${level}`; the orange underline
  now lives in `_tabs.css` so it can never leak.
- `Pagination/Pagination.tsx` — active pager is a **navy fill / white text**, not a lime tint.
- `Tooltip/Tooltip.tsx` — navy fill, static class strings (was interpolated → unscannable).
- `MenuItem/index.tsx` — default item height `35 → 32` (dropdown items).
- `Alert/Alert.tsx`, `StatusIcon/StatusIcon.tsx` — emerald/blue/yellow/red swapped for the
  four Aidi status tokens, with lifted dark-theme values.

### Shell (`src/components/template/`, `src/components/layouts/`)
- `Logo.tsx` — rewritten: Aidi wordmark navy/white at 26 px + a 1 px divider + the
  `TENANT_NAME` eyebrow (11 px/800 `#8A929F`); `type="streamline"` → the 24 px monogram.
- `SideNav.tsx`, `MobileNav.tsx` — the `navMode === 'themed' | 'transparent'` branches no
  longer read `themeColor`; the nav is white in light and `#0C1033` in dark, full stop.
- `VerticalMenuContent.tsx` (10 px rail padding, 34 px items), `VerticalMenuIcon.tsx` (20 px),
  `VerticalSingleMenuItem.tsx` / `VerticalCollapsedMenuItem.tsx` (2 px item gap).
- `ClassicLayout.tsx` — header `shadow` class removed; content wrapper gets the `#FAFCFD` page fill.
- `Header.tsx` — unchanged (all styling moved into `_header.css`).
- `UserDropdown.tsx` — 32 px navy avatar with white initials, 13 px/800 navy name,
  11 px/800 `#8A929F` role eyebrow, email wrapped `dir="ltr"`.
- `Notification.tsx` — bell → Lucide `Bell` 20 px; the red count pill is now a **6 px orange
  dot** (a sanctioned orange touchpoint); avatar tints → Aidi status tokens.
- `SidePanel/SidePanel.tsx` — Lucide `Settings` 20 px; `SidePanelContent.tsx` now actually
  renders the configurator (it was a `<div>Content</div>` stub).
- `ThemeConfigurator/ThemeConfigurator.tsx` — trimmed to **ModeSwitcher + DirectionSwitcher**.
  **Deleted:** `ThemeSwitcher.tsx`, `LayoutSwitcher.tsx`, `NavModeSwitcher.tsx`, `CopyButton.tsx`.
- `LanguageSelector.tsx` — the selected-language check is azure, not emerald.
- `src/utils/hooks/useTwColorByName.ts` — the 12 stock Tailwind hues replaced with the
  8-hue Aidi categorical ramp (navy → ink → azure → lavender → slate → viz-3).

### Auth
- `layouts/AuthLayout/Simple.tsx` — rewritten as the split sign-in shell: navy gradient brand
  panel (orange radial glow, 7 %-opacity repeating mark watermark, product statement, a
  governed/audited strip in azure-300) + a `#FAFCFD` form panel holding the Aidi lockup, the
  tenant eyebrow, a white 8 px bordered card and an audit-log note beside the language selector.
- `layouts/AuthLayout/AuthLayout.tsx` — stopped cloning a Fragment with invalid props; the
  language selector moved into the layout. ForgotPassword / ResetPassword / SignUp inherit the
  same shell, so none of them look broken.
- `views/auth/SignIn/SignIn.tsx` — 22 px/800 navy heading + a 13 px muted sub-line.
  `SignInForm.tsx` is **untouched** — same fields, names, validation, i18n keys, `useAuth().signIn`.

---

## 4. Orange budget (audited)

Sanctioned and shipped: **(1)** side-nav active rail — 3 px `#FF6600` on the leading edge
(`inset 3px 0 0` LTR / `inset -3px 0 0` RTL) over a `#FFF4EC` fill with a navy-800 label and an
orange icon; **(2)** the unread notification dot, 6 px; **(3)** the active tab underline, 2 px;
**(4)** `.card-lead` (available, unused until the Phase B KPI row). The `signal` Button colour
exists and nothing calls it yet, as specified. The login brand panel carries a very low-opacity
orange radial glow — well under the 2 % pixel budget on a panel that is half the viewport.

---

## 5. Safelist

`twSafelistGenerator` now receives an explicit list instead of `{colors}` (every Tailwind hue):

- **Colours:** `navy, gray, azure, signal, success, warning, danger, info` + **`red` and `blue`**
  — added because 31 call sites pass `color="red-600"` and 1 passes `color="blue-600"` to
  `Button`, which builds `bg-${color}-${level}` at runtime. Dropping them would have made every
  delete button transparent.
- **Prefixes:** the 18 the runtime actually builds (`text/bg/ring/border`, their `hover:`,
  `active:`, `focus:`, `focus-within:` and `dark:` forms).
- **Metrics:** `h-{7,8,9,10}` / `w-{7,8,9,10}` as literals rather than the whole `{height}` /
  `{width}` scales. A grep of `src/components/ui/**` confirms `CONTROL_SIZES` is the only
  source of runtime `h-${…}` / `w-${…}` strings (Button, Input, Segment, InputGroup/Addon, FormItem).
- **Avatars:** the 8 `useTwColorByName` hues, `bg-` + `dark:bg-`.
- **Literals:** `text-night-raised` / `dark:text-night-raised` (built from a Tooltip prop).

4,327 → 1,231 classes; CSS 556 kB → 264 kB.

---

## 6. Deviations from the brief, and why

1. **`Logo` uses `aidi-wordmark-*.svg`, not `aidi-logo-*.svg`.** The `aidi-logo` artwork is a
   stacked lockup with a 803 × 947 viewBox — at the specified 28 px height it is ~24 px wide and
   the wordmark inside it is illegible. The wordmark file is the horizontal lockup (793 × 425),
   which is what `design-reference/login.html` itself uses. Rendered at 26 px in the nav header
   and 24 px on the login page.
2. **`aidi-enterprise.css` was edited in three places** rather than dropped in verbatim:
   (a) the dark block's selector is now `.dark, [data-theme="dark"]` so the template's `dark`
   class on `<html>` activates it; (b) the `[lang="ar"]` rule no longer forces
   `font-family: "Manifa V2"` — that would have pulled numerals into Manifa, contradicting
   "numerals stay Western-Arabic in Anodina". It now sets only `letter-spacing: 0` and
   `line-height: 1.7` on `[dir="rtl"], [lang="ar"]`, with the face coming from the stacked
   `font-sans`; (c) the blanket `a { color: azure }` rule was removed and replaced with
   `.e-link` / `.text-link` — nav items, menu items and buttons are all anchors and must
   inherit their colour, not turn azure. The defensive `[dir="rtl"] h1..h6` rule from
   `rtl-arabic.md` was added.
3. **`borderRadius` and `boxShadow` were not merged verbatim** from `tailwind.aidi.cjs`. That
   file names its scale `sm:6 md:8 lg:12`, which would have shifted every existing
   `rounded-md`/`rounded-lg` in the 34 component stylesheets by one step. I followed the brief's
   explicit map instead (`DEFAULT/md = 6`, `lg = 8`, `xl = 12`) and kept the Tailwind default
   names the CSS relies on, adding `xs` and `pill` alongside.
4. **`aidi.fontWeight` was not merged.** `{light:300, normal:400, extrabold:800}` replaces the
   whole weight scale, which would break the ~200 `font-semibold` / `font-bold` classes across
   the app. Left alone deliberately: CSS font matching resolves 600 and 700 up to Anodina 800
   and Manifa 700, so nothing synthesises a faux bold anyway.
5. **`fontFamily` sits at `theme.fontFamily`, not `theme.extend.fontFamily`** — the template
   declares it at the top level, so extending would not have overridden Cairo.
6. **No "forgot password" link was added to the login card.** The brief asks for an azure
   tertiary link, but the `ActionLink` to `/forgot-password` is commented out in
   `SignInForm.tsx` and the brief also says to keep the form exactly as-is. Uncommenting it is
   a one-line change whenever the route is wanted.
7. **`Button` disabled state is still `opacity-50`**, not the spec's `#F2F6F8` fill + `#D7E1E8`
   border + `#8A929F` text. Changing it means restructuring `getBtnColor`, which would alter
   every variant's class output; deferred rather than risked.
8. **`.avatar`'s default fill is `gray-300`, not navy.** Navy is applied explicitly where the
   spec asks for it (UserDropdown, notification avatars). A navy default turned every
   placeholder avatar in the users table into a heavy solid blob.
9. **The side nav stays a persistent, collapsible rail** rather than the spec's overlay drawer —
   as instructed, for a table-heavy CRM.

---

## 7. Known issues left for Phase B

### Hard-coded colours in `src/views/**` + `src/components/shared/**`

| Hue | Occurrences | Files | Note |
| --- | --- | --- | --- |
| `red-*` | 111 | 79 | Mostly delete/danger. **Already re-tuned** by the `red` scale override — audit, don't rewrite. |
| `blue-*` | 89 | 9 | → `azure-*` / `info` |
| `emerald-*` | 88 | 41 | → `success` / `success-tint` |
| `green-*` | 56 | 30 | → `success` |
| `orange-*` | 17 | 5 | **Orange-budget violations** — see list below |
| `amber-*` | 14 | 7 | → `warning` |
| `indigo-*` | 10 | 3 | **Already aliased to `navy`** — safe, but rename for clarity |
| `purple-*` | 8 | 2 | → `lavender-500` / `viz-5` |
| `sky-*` | 8 | 2 | → `viz-3` / `viz-4` |
| `teal-*` | 8 | 1 | → `viz-3` |
| `pink-*` | 8 | 1 | → `viz-6` |
| `yellow-*` | 6 | 3 | → `warning` |
| `rose-*` | 2 | 1 | → `danger` |
| `lime-*` | 0 | 0 | ✅ fully gone |

Raw hexes still in views: `#ef4444`, `#df0018`, `#10b981`, `#1da1f3`, `#1773ea`, `#0077b5` (6 sites).

### Specific Phase B targets spotted while working

- **Orange misuse (5 files):** `views/clients/detail/tabs/Details.tsx:144` (`bg-orange-600` Tag),
  `views/clients/detail/components/Timeline.tsx:195`, `views/clients2/list/ClientsTable.tsx:158`,
  `views/dashboard2/components/KPICollector.tsx:152,297`,
  `views/dashboard2/components/AllCollectorsKPI.tsx:323`. Plus a full-width tan/orange
  "قفل العملاء المحددين" button on `/clients`. All of these need to become status tints; the
  screen's orange budget is already spent on the nav rail and the tab underline.
- **Row-level fills on `/clients` and `/archive`** — whole rows painted teal / red / green
  (`rgb(72,176,177)` etc.) from view logic. The spec wants a status badge in a status column
  plus a hairline, never a painted row. This is the single biggest visual gap left.
- **`src/components/shared/ActionLink.tsx`** still uses `useThemeClass().textTheme`, so links
  render navy instead of azure `#234BB0`. One-line fix, but it is a `shared/` file.
- **`src/components/shared/GrowShrinkTag.tsx`, `UsersAvatarGroup.tsx`, `SegmentItemOption.tsx`**
  — status/colour logic to move onto the new `.tag-*` classes.
- **Status pills:** `_tag.css` now exports `.tag-{success,warning,danger,info,neutral}` and
  `.tag-meta`. Nothing in `views/` consumes them yet; each status column should pick one and
  add the distinct glyph shape (dot / diamond / bar / ring) the spec asks for.
- **Numeric alignment:** `font-variant-numeric: tabular-nums` is now global on `td`/`th`, but
  numeric columns are still leading-aligned in most tables — they should be trailing-aligned,
  with currency/units moved into the column header.
- **KPI cards:** `.card-lead` (the 3 px orange inset rail, RTL-aware) is ready; one card per
  row in the dashboard should take it, and KPI values should go to 32 px/800 navy.
- **Apex charts** (`vendors/_apex-chart.css`, `shared/Chart.tsx`) still use the stock palette —
  they should take `viz-1..8` with `viz-highlight` reserved for the user's selection.
- **Collapsed side nav is not sticky** (`side-nav-expand` is only applied when expanded), so on
  a long page the collapsed icon rail scrolls away. Pre-existing template behaviour, unchanged.
- `PAGE_CONTAINER_GUTTER_Y` is `'py-4 sm:py-6 md:px-8'` — the `md:` breakpoint sets `px`, almost
  certainly a pre-existing typo for `py`.

---

## 8. Screenshots

`logs/screens/phaseA/` — `00_sign-in`, `01_home` … `16_client_detail`, plus
`_dark_home`, `_dark_users`, `_dark_signin`, `_collapsed_view`, `_collapsed_nav`,
`_zoom_header`, `_zoom_nav`, `_crop_users_head`.

Verified by reading computed styles in the running app (light **and** dark):

| | Light | Dark |
| --- | --- | --- |
| body | `#FAFCFD` / `#2E3450` | `#060820` / `#C7CDE4` |
| side nav | `#FFFFFF`, 230 px, 1px `#D7E1E8` on the inline-end (left in RTL) edge | `#0C1033`, 1px `#262C5E` |
| header | `#FFFFFF`, 56 px, 1px bottom border, no shadow | `#0C1033`, 1px `#262C5E` |
| table header | `#DFEEF2`, 11 px/800 `#070A4B` | `#141A45` |
| card | `#FFFFFF`, 1px `#D7E1E8` | `#0C1033`, 1px `#262C5E` |
| active nav item | `#FFF4EC`, `inset -3px 0 0 #FF6600` (RTL), `#070A4B` 800, 34 px | `#241634`, `inset -3px 0 0 #FF8433`, `#F2F6F8` |
| table cell | 44 px, 13.5 px, `#2E3450`, `#E7EEF2` hairline, `tabular-nums` | same metrics |
