# CAWP frontend → Aidi AI Enterprise — Phase B

**Scope:** `src/views/**`, `src/components/shared/**` (including `DataTable.tsx`),
`src/components/template/Notification.tsx`, `src/constants/chart.constant.ts`,
`src/configs/chart.config.ts`, `src/assets/styles/vendors/_apex-chart.css`, and
`tailwind.config.cjs` **for colour scales only**. Phase A owns tokens, fonts,
`src/components/ui/**`, the app shell, login and the safelist — untouched here.

**Status:** `npm run build` passes clean (no warnings). `npx tsc --noEmit` = **5,315 →
5,134 lines** — zero new type errors; the count fell because dead code was removed.
Verified live on `/home`, `/clients`, `/archive`, `/users`, `/payment`,
`/user_collection`, `/report/users/comments`, `/report/users/paymentDates`,
`/report/quality/neglect`, `/portfolios`, `/contracts`, `/permissions`, `/supervisor`,
`/client_query`, `/settings/general/archiveReasons` and the client-detail tabs, in
light **and** dark.

---

## 1. Headline numbers

| | Phase A | Phase B |
| --- | --- | --- |
| `build/assets/index-*.css` | 263,834 B | **267,148 B** (+3.3 kB, +1.3 %) |
| entry chunk `index-*.js` | ~360,000 B | **372,608 B** (+~13 kB) |
| vendor chunk | 583,260 B | 580,572 B |
| `build/` total | 8.0 MB | **8.0 MB** |
| `safelist.txt` | 1,231 classes | 1,231 (unchanged — no new runtime-built colours) |
| `tsc` lines | 5,315 | **5,134** |
| Files changed | — | **~194 in `src/views/**`**, 36 in `src/components/shared/**`, 1 template, 3 config/style |
| New files | — | `shared/statusColor.ts`, `shared/StatusPill.tsx`, `views/dashboard2/components/StatTile.tsx` |

The CSS grew by 3.3 kB because six stock Tailwind hues now resolve to real ten-step
Aidi ramps instead of being tree-shaken as unused, plus the new `.tag-*`/rail/KPI
utilities. No new safelist entries were needed: everything added is a literal class in
source, and nothing new is built at runtime from a prop.

---

## 2. Status-coloured rows (item 1)

The DB-driven row colour (`CommentStatuses.color` / `status.color`) used to paint the
**entire row** teal / red / green with white-or-black text picked by luminance. It now
renders enterprise-style, and — because the painting lived in one place
(`DataTable.tsx`) — every table in the app changed at once: `/clients`, `/clients2`,
`/archive`, all nine report tables, collections and accounts.

**`src/components/shared/statusColor.ts`** (new, 231 lines) — `statusTint(color, scheme)`
parses `#rgb` / `#rrggbb` / `rgb()` / `rgba()` / a few CSS names and returns:

| key | light | dark |
| --- | --- | --- |
| `rail` | the raw colour | lifted toward white until ≥ 3:1 on `#0C1033` |
| `tint` | 10 % of the colour over white — the **row fill** | 22 % of the colour over `#0C1033` |
| `pill` | 20 % over white — the **pill fill**, so a pill still reads as a pill on a tinted row | 38 % over `#0C1033` |
| `text` | darkened until ≥ **4.5:1 on white** | lightened until ≥ 4.5:1 on `#0C1033` |

Contrast was checked numerically for ten inputs (including `#ffff00`, `#00ff00`,
`#000000`, `#ffffff`): every `text` value clears 4.5:1 in both schemes. Results are
memoised per `scheme|colour`. Unparseable input returns a neutral set rather than
throwing.

**Row rendering (`DataTable.tsx`):**
- 3 px rail on the **leading edge** of the first cell —
  `ltr:shadow-[inset_3px_0_0_var(--row-rail)] rtl:shadow-[inset_-3px_0_0_var(--row-rail)]`
  with the colour handed in as a `--row-rail` custom property. Flips under RTL; verified
  in the browser (rail lands on the **right** edge in Arabic).
- Row fill = `tint`. Row text stays `#2E3450` from `_tables.css` — no more white-on-teal.
- The status column renders as a **pill** (22 px, 12 px/800, `pill` fill, `text` label,
  7 px dot in the raw colour). `DataTable` recognises the status column by normalising
  the TanStack column id (`comment_status..name` → `comment_status__name` →
  `comment_status_name`), so it also catches `status..name` and `comment_status_name`.
- **Selected rows keep the azure rail** from `_tables.css`: when `row.getIsSelected()`
  the inline rail and tint are skipped and `table-row-selected` is applied instead
  (that class existed in Phase A's CSS but nothing was ever adding it — now it is).

Non-comment-status row colouring got the same treatment: locked clients and archived
rows on `/clients` and `/archive` (`is_locked` / `is_archive`) now read as a
`StatusPill` plus the icon rail rather than a coloured text run, and `LockHistory`'s
hard-coded `borderLeftColor: '#ef4444' | '#10b981'` became `border-s-danger` /
`border-s-success` (logical, so it flips).

---

## 3. Hue re-mapping (item 2)

Done entirely in `tailwind.config.cjs` → `theme.extend.colors`, so ~300 existing class
usages re-tuned with no file sweep.

| Stock hue | Occurrences | → Aidi ramp | 50 | 100 | 500 | 600 | 700 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `emerald` (88), `green` (56) | 144 | **success** | `#E4F2EC` | `#CDE8DA` | `#128A5C` | `#0F7A52` | `#0B5F40` |
| `blue` (89), `sky` (8) | 97 | **azure** | `#EEF3FD` | `#D9E4FA` | `#2F5FD6` | `#234BB0` | `#1A3A8A` |
| `orange` (17), `amber` (14), `yellow` (6) | 37 | **warning** | `#FBEBDD` | `#F7DCC2` | `#C86A00` | `#B25A00` | `#8F4800` |
| `rose` (2), `pink` (8) | 10 | **danger** (= `red`) | `#FDF6F5` | `#F9E6E4` | `#B3261E` | `#B3261E` | `#8F1E17` |
| `teal` (8), `cyan` | 8 | **steel** | `#EFF5FA` | `#DCE9F3` | `#5B93C9` | `#4176A8` | `#325C84` |
| `purple` (8), `violet` | 8 | **lavender** | `#F3F2F8` | `#E7E5F0` | `#8E86B8` | `#736A9C` | `#5A527B` |
| `neutral` | 15 | **= `gray`** (Aidi neutrals) | `#FAFCFD` | `#F2F6F8` | `#8A929F` | `#6B7482` | `#2E3450` |
| `indigo` (10) | — | already aliased to `navy` in Phase A | | | | | |
| `red` (111) | — | already the Aidi destructive ramp from Phase A | | | | | |

All ten steps (50–950) are filled for each ramp so `*-200`, `*-300`, `*-800` etc. still
resolve. **Brand orange `#FF6600` is unreachable from any hue name** — it lives only in
`signal-*` and in the three CSS rails.

**The five orange-budget files were hand-fixed**, not left to the remap:

| File | Was | Now |
| --- | --- | --- |
| `views/clients/detail/tabs/Details.tsx:144` | `<Tag className="text-white bg-orange-600">` | `StatusPill tone="neutral" size="md"` |
| `views/clients/detail/components/Timeline.tsx:195` | `bg-orange-100 text-orange-800 border-orange-200` (+ a 🕒 emoji) | `.tag-warning` + `HiOutlineClock`, emoji removed. The row avatar `bg-amber-500` → `bg-navy-600` |
| `views/clients2/list/ClientsTable.tsx:158` | `text-orange-500` / `text-green-500` | `StatusPill tone="warning" / "success"` |
| `views/dashboard2/components/KPICollector.tsx:152,297` | `bg-orange-100 …` count badge + avatar | neutral `.tag` pill / `bg-gray-100 text-gray-600` avatar |
| `views/dashboard2/components/AllCollectorsKPI.tsx:323` | `bg-orange-100 …` "days remaining" badge | neutral `.tag` pill |

Plus the full-width tan **"قفل العملاء المحددين"** button on `/clients` — it was
`variant="solid" color="red-600"`/`"green-600"`, and `green` was never in the safelist so
the unlock twin rendered as a transparent ghost. Both are now `variant="default"` with
lock icons.

**Final audit:** `grep -rn "bg-orange\|text-orange\|border-orange" src/views src/components/shared | wc -l` → **0**.
There are no survivors to justify. `signal-500` survives in exactly two places, both
sanctioned: the notification bell dot and the unread-item dot in `Notification.tsx`.

### Per-page orange count (measured in the browser, not grepped)

Every element whose computed `color` / `background` / `border` / `box-shadow` / `fill` /
`stroke` contains `#FF6600`, `#FF8433`, `#E65B00` or `#CC5200`, de-duplicated so a rail
and the icon inside it count once. Budget is ≤ 4.

| Page | Orange touchpoints | What they are |
| --- | --- | --- |
| `/home` | **2** | side-nav active rail · the one lead-KPI rail (`.card-lead`) |
| `/clients` | **1** | side-nav active rail |
| client detail | **1** | active tab underline |
| `/archive`, `/users`, `/portfolios`, `/contracts`, `/client_query`, `/user_collection`, `/permissions`, `/report/users/comments`, `/report/users/paymentDates` | **1** each | side-nav active rail |
| `/payment`, `/report/quality/neglect`, `/supervisor`, `/settings/**` | **0** | (their nav parent is collapsed, so no rail is active — pre-existing nav-matching behaviour) |

The notification dot adds one more on any page where there is unread mail. Worst case
observed: 3 of 4.

---

## 4. Toolbars (item 3)

`/clients` had **13 solid navy buttons** stacked over two rows. It now reads:

- **Leading edge:** `بحث` (filter toggle, `variant="default"` + search icon), and the
  conditional `إلغاء الاستفسار` as a destructive secondary.
- **Trailing edge (`ms-auto`, `gap-2`, `flex-wrap`):** `تصدير` · `تخصيص لمستخدم` ·
  `نقل للارشيف` · `قفل العملاء المحددين` · `فتح قفل العملاء المحددين` ·
  `حذف` (secondary with a red trash icon) · the **`عمليات ملف إكسل` dropdown** ·
  **`اضافة عملاء`** — the single navy primary.

The Excel dropdown holds all seven bulk actions (`تخصيص العملاء بملف اكسل`,
`نقل العملاء للارشيف بملف اكسل`, `استفسار بملف اكسل`, `ارسال تنبيه`,
`تحديث بملف اكسل`, `إضافة/تحديث خاصية بملف اكسل`, `اضافة متابعات بملف اكسل`), each with
a 16 px icon and the same permission gate as before.

**How the dropdown keeps every modal mounted:** `ui/Dropdown`'s menu *unmounts* when it
closes (`AnimatePresence` + `{!hidden && …}`), so putting the action components inside it
would have destroyed their dialogs on close. Instead each component stays mounted in the
toolbar inside `<div className="hidden" aria-hidden="true">` with a ref, and each
`Dropdown.Item` fires `ref.querySelector("button")?.click()`. `react-modal` portals to
`document.body`, so the dialog renders normally from a `display:none` parent. Verified
live: the menu lists 7 items and clicking one opens the original dialog with its upload
control, selects and submit handler intact. **No component's internals were touched, no
action was removed, and every permission check is byte-identical.**

The same pattern was applied to `/archive` (2 items: `استفسار بملف اكسل`,
`نقل من الارشيف`). `/client_query` had no toolbar to restructure.

Across the rest of the app:
- 17 export triggers, 22 dialog triggers and ~15 dialog "cancel" buttons went
  `variant="solid"` → `variant="default"`; the red-solid cancel buttons lost
  `color="red-600"` (a solid destructive Cancel is a mis-signal).
- 12 list pages keep exactly one navy `{t("add")}` primary; the `block` prop that made
  them full-width was dropped, along with the `block lg:inline-block md:mx-2 md:mb-0 mb-4`
  wrapper divs.
- 12 report/collection pages have **no** primary — they have no create action.
- Destructive actions are `variant="default"` + `!text-red-600` + `HiOutlineTrash`.
- 28 leftover MUI props (`variant="contained"`, `color="secondary"`, `color="error"`)
  were doing nothing at all on the ui `Button` — they now map to real variants.

---

## 5. DataTable (item 4)

`src/components/shared/DataTable.tsx`, 440 → 534 lines.

- **Header** 38 px on `#DFEEF2`, 11 px/800/+0.09em navy (tracking zeroed under RTL by
  Phase A's CSS), and now `whitespace-nowrap` — three-line headers were pushing the row
  to 60 px. Measured: every `th` is exactly **38 px**.
- **Sort icons** 12 px `#8A929F`, azure `#2F5FD6` when active. The ui `Sorter` hard-codes
  `text-navy-600` on the active glyph, so it is wrapped in a
  `[&_svg]:!text-current` span rather than editing the primitive.
- **Numeric columns** — new column hint `meta: { isNumeric: true }` renders the cell
  `text-end tabular-nums` and right-aligns the header (`!text-end`, needed to beat
  `_tables.css`'s `text-left rtl:text-right` at specificity 0,1,3). Added to the
  amount / count / day columns of 25 tables. `meta: { isId: true }` gives the
  identifier column the navy-800 tabular treatment. `tabular-nums` is already global on
  `td`/`th` from Phase A, so untagged columns are unaffected.
- **Checkbox column** 44 px (`w-11`) on both header and cells.
- **Loading** is a skeleton at the real 44 px row height in `#F2F6F8`
  (`loaders/TableRowSkeleton.tsx` retuned from `gray-200` to `gray-100`, bar height 10 px),
  capped at 12 rows so a 1000-row page size does not render 1000 skeletons.
- **Empty state** — `لا توجد بيانات` (the existing `noData` key), 13.5 px `#6B7482`,
  centred in a 132 px (44 × 3) cell.
- **Footer** — `#FAFCFD` strip, 1 px top border, 12/20 px padding. Range label
  `الصفوف 1–100 من 177,862` on the leading edge, 12.5 px `#6B7482`, tabular,
  `flex: 0 0 auto` + `whitespace-nowrap`. Pager and the 32 px page-size select on the
  trailing edge. The old English `Total N Items` from `ui/Pagination`'s `Total`
  component is switched off (`displayTotal={false}`) rather than edited.
- **Totals row** (`TFoot`) 13.5 px/800 navy tabular on `#FAFCFD`.
- **Bug fixed:** 27 of the 28 call sites pass `pagingData.page`, but the component only
  read `pagingData.pageIndex` — so `currentPage` was always `undefined` and the pager
  never highlighted the page you were on. It now reads `pageIndex ?? page ?? 1`. This is
  the one behaviour-visible change in the file; it was needed for the range label to be
  correct and no call site was modified.

---

## 6. Dashboard (item 5)

`src/views/dashboard2/**`, 9 components + a new `StatTile.tsx`.

- **`StatTile`** — eyebrow 11 px/800 `#8A929F`, value 24 px → 32 px/800 navy tabular
  (`-0.02em`), unit 12.5 px muted on the baseline, white card / `rounded-lg` / 1 px
  `#D7E1E8` / no shadow. A `lead` prop adds `.card-lead` (the 3 px `#FF6600` inset rail,
  RTL-aware). Laid out 2 × 2 rather than 4-up: at 32 px, `473,674.26` is ~190 px wide and
  a 4-up row in a half-width card gives each tile 118 px.
- **Exactly one lead rail per screen.** `السدادات اليومية` (today's collected total) has
  it; `المواعيد اليومية` deliberately does not — two side-by-side cards each claiming a
  lead rail would have taken `/home` to 3 orange touchpoints before the notification dot.
- **Count badges** (122 / 22 / 12 / 10) → neutral `.tag` pills at 22 px tabular, with a
  single azure `.tag-info` pill on `الأيام المنقضية` — the one value that moves during
  the month.
- **Section titles** 15 px/800 navy with a 12.5 px `#6B7482` tabular sub-line (the date).
  The page greeting is 20 px/800 navy and lost its exclamation mark
  (`مرحباً, X!` → `مرحباً، X`).
- **The two daily lists** are 44 px rows with `#E7EEF2` hairlines, no zebra; name
  navy 800, contract muted, amount 13.5 px/800 tabular on the **trailing** edge with the
  `SaudiRiyal` glyph. An empty state was added (previously the card just ended).
- **Collector KPI cards** — bordered, no shadow, name 13.5/800 navy, labels 12.5 muted,
  values tabular, `achieved` = `text-success`, `remaining` = danger/success by sign.
- Export buttons are `variant="default"`; card gaps 16 px, section gaps 32 px
  (`mt-section`), card padding 20 px (`p-card`).

---

## 7. Charts (item 6)

- **`constants/chart.constant.ts`** — dropped `twin.macro`'s `theme\`colors\`` for
  literal hexes so the palette cannot drift when a stock hue is re-mapped. `COLORS` is
  now the 8-hue Aidi ramp
  `#070A4B · #2F5FD6 · #5B93C9 · #8FB9CE · #8E86B8 · #B2AAC4 · #6B7482 · #B8C4CE`,
  matching `viz-1..8`. `COLOR_HIGHLIGHT = #FF6600` is exported and **assigned to nothing**
  — no chart in the app expresses a selected/hovered/filtered series today. Every previous
  export is still exported (nothing removed); `COLORS_DARK`, `RAMP_SEQUENTIAL`,
  `RAMP_DIVERGING`, `GRID`, `AXIS`, `LABEL`, `TOOLTIP_*`, `ZERO_LINE`, `TARGET_LINE` added.
- **`configs/chart.config.ts`** — horizontal grid only (`#E7EEF2`, no vertical rules on a
  time series), axis line `#D7E1E8` / ticks `#8A929F`, labels 11.5 px `#6B7482` in Anodina
  with `cssClass: 'apex-axis-label'` (tabular), legend 12 px `#2E3450` above the plot with
  8 × 8 radius-2 markers, bars `borderRadius: 2` / `columnWidth: '70%'` / 4 px grouped
  gap, line stroke 2, data labels off.
- **`vendors/_apex-chart.css`** — tooltip navy `#070A4B` / white 12.5 px / radius 6 /
  `0 8px 24px rgba(7,10,75,.10)` / no border; plus full dark-theme variants
  (`.dark`, `[data-theme="dark"]`): grid `#1B2049`, labels `#6E769B`, legend `#C7CDE4`,
  tooltip `#141A45` on `#F2F5FB`.
- **`shared/Chart.tsx`** — the RTL wrapper still forces `direction: 'ltr'` on the chart
  container, which is what stops the **time axis from mirroring**; a comment now says so.
  Legends do mirror (`legend.style.direction = 'rtl'`). Props API unchanged. Fixed a
  latent crash: `querySelectorAll('div.apexcharts-legend')[0]` can be `undefined` (donut,
  sparkline, `legend: {show:false}`, or before Apex's async render) and every branch then
  dereferenced `.style` — now `querySelector` + early return.
- **`TaskOverview.tsx`** — real bug fixed: the legend swatches were
  `bg-indigo-600` / `bg-emerald-500` while the chart drew `[COLORS[0], COLORS[2]]`.
  `indigo` aliases to navy so the first matched by luck; `emerald` now maps to success
  `#128A5C` and no longer matched the steel `#5B93C9` series. They are `bg-navy-600` and
  `bg-viz-3`. Since the chart sets `legend: {show:false}`, that row *is* the legend — a
  mismatched swatch there is a misread chart.

---

## 8. Status pills & badges (item 7)

**`src/components/shared/StatusPill.tsx`** (new) — `tone` ∈
`success | warning | danger | info | neutral`, optional raw `color`, `size: 'row' | 'md'`
(22 / 24 px), built on Phase A's `.tag-*` classes. Each tone carries the glyph shape from
`components.md` so the set survives greyscale and CVD:

| tone | glyph |
| --- | --- |
| success | 7 px filled circle |
| warning | 7 px square rotated 45° (diamond) |
| danger | 8 × 2 px bar |
| info | 7 px circle, 2 px ring, hollow |
| neutral | 7 px circle in `#8A929F` |

Passing `color` overrides the palette (tint fill, darkened label, dot in the raw colour)
while `tone` still chooses the shape — that combination is what the DB-driven comment
statuses use.

Placed at: payment **approval status** (`AprrovePaymentButton`, both collections
variants), comment-status **active/inactive** in settings, **archive reason** on
`/archive`, `is_archive` on `/client_query`, client-detail **payment status**,
**call disposition**, **lock/unlock history**, **timeline comment status** and
**reachability**. Every `comment_status..name` / `status..name` column is handled
automatically by `DataTable` and was deliberately *not* double-wrapped.
`GrowShrinkTag` moved from `growShrinkColor()`'s emerald/red classes onto
`.tag-success` / `.tag-danger` / `.tag-neutral`. No underlying value or label changed.

---

## 9. Client detail (item 8)

24 files under `src/views/clients/detail/**`.

- Page header: `اسم العميل` eyebrow → name 20 px/800 navy → identifiers row with the
  account number and national ID each `dir="ltr"` + `tabular-nums` + `truncate` + `title`.
- The `is_locked` badge became a persistent warning `Alert` (same copy, same condition);
  `is_archive` became a neutral `StatusPill`.
- The orange tab underline was verified rendering; there are no nested/secondary tab rows
  in the directory, so no azure override was needed.
- `Timeline.tsx` — 44 px rows with hairlines and `hover:#FAFCFD`, comment status as a
  `StatusPill` driven by the DB colour, card-in-card-in-card flattened, and a real markup
  bug fixed (a `<div>` inside a `<p>`, which the browser was auto-closing and breaking the
  flex row).
- `Comments.tsx` — the timeline card is now a sibling of the form card, not nested.
- Payment / payment-date / call-record / specify-user tables — amounts and durations
  `!text-end` tabular navy-800, dates tabular, phone numbers `dir="ltr"`.
- Contact list — 44 px rows, phone and email `dir="ltr"` tabular with truncation.
- `LockHistory.tsx` — the hard-coded `#ef4444` / `#10b981` left border is now
  `border-s-danger` / `border-s-success`; confirmed in the browser that
  `border-inline-start` lands on the **right** edge under RTL.
- Directory-wide sweep: zero `shadow*`, zero `rounded-xl/2xl/3xl`, zero `#hex`, zero
  `orange`, zero physical `ml-/mr-/pl-/pr-/left-/right-/text-left/text-right/border-l/r`,
  zero emoji.

---

## 10. Sweep (item 9)

Across `src/views/**` and `src/components/shared/**`, all now **zero hits**:

- `shadow-sm|md|lg|xl` on cards and panels — removed (elevation 0 is a border).
  The 14 advanced-search panels went from `border p-5 pb-12 shadow-lg` to a real card.
- `rounded-xl|2xl|3xl` on cards → `rounded-lg`.
- Hard-coded `#rrggbb` in `style={{}}` and `text-[#…]` — the only two survivors are the
  `color` and `borderColor` props handed to `react-dropdown-select`, a third-party
  component that only accepts literal colours (now azure `#2F5FD6` and `#B8C4CE` instead
  of the string `"navy"`, which the browser was resolving to CSS `navy` `#000080`).
- `hover:${textTheme}` and friends — **43 files** were building Tailwind classes by
  string interpolation, which the JIT scanner cannot see, so those hovers had been dead
  since the template was forked. Replaced with the literal `hover:text-azure-600`.
- `ActionLink` now uses `.text-link` (azure `#234BB0`) instead of `useThemeClass()`'s
  navy; `SegmentItemOption`'s selection ring is azure, not the theme colour.
- Physical → logical (`ms/me`, `ps/pe`, `text-start/end`, `border-s`) everywhere touched;
  several `ml-2 rtl:mr-2` pairs were applying *both* margins under RTL.
- `font-semibold`/`font-bold` on numerics also got `tabular-nums`.
- Latin identifiers inside Arabic (account numbers, national IDs, emails, phones, UUIDs)
  get `dir="ltr"` + `tabular-nums` + `truncate max-w-[190px]` + `title` — 16 table files
  plus client detail. The UUID account-number column on `/clients` was wrapping over four
  lines and forcing 86 px rows; it is now one line with an ellipsis and a tooltip.
- Dialog footers: the MUI leftovers `<div class="mt-4 ltr:text-right"><div class="stack">`
  (invalid `class=` in JSX, so never applied) became
  `className="mt-4 flex flex-wrap items-center justify-end gap-2"` in 18 dialogs, so the
  buttons are genuinely on the inline-end edge.
- Both `ConfirmDialog`s: title 15 px/800 navy, footer on a `#FAFCFD` strip with a 1 px top
  border, Cancel is a real secondary.

---

## 11. Notification dropdown (item 10)

White card, radius 8, 1 px `#D7E1E8`, `--e-el-2` — inherited from `.dropdown-menu`, with
`overflow-hidden` added so the header and footer clip to the radius. Header is a 44 px row
with a `#E7EEF2` bottom hairline and a 15 px/800 navy title. Items `min-h-[44px]`,
13.5 px body, hairline between, none on the last. Timestamp 12 px tabular muted. The
in-body client link is `.text-link` instead of `text-blue-600 underline`. Footer is a
44 px row with an azure 13.5 px/800 link. **Unread = the 6 px `#FF6600` dot, read = no
dot** (previously read items showed a grey dot). Empty state: a muted 20 px Lucide `Bell`
and calm copy — no illustration, no emoji, no exclamation mark. Handlers, routes and i18n
keys unchanged.

---

## 12. Skipped, and why

1. **Dialog button order left as primary-then-secondary.** `components.md` asks for
   "secondary then primary" in the footer. Every dialog in this app has shipped with the
   primary first; reordering ~30 dialogs changes muscle memory for a daily-use collections
   tool without a visual-system payoff. Alignment and variants were fixed; order was not.
2. **`/portfolios` and `/permissions` have no primary button.** Their `{t("add")}` link
   and the `/permissions` `is_active` status column are **commented out in source**.
   Uncommenting is a behaviour change, so they were left alone; `/portfolios` has an
   actions group with only the export button.
3. **`/contracts` gets no `StatusPill`** — its list has no active/inactive column
   (name / count / total only).
4. **Dark-theme chart *series* still use the light ramp.** `COLORS_DARK` is exported and
   ready, but ApexCharts takes `colors` once at construct time and the app has no
   chart-level theme observer; swapping the ramp on a `.dark` toggle needs a re-render
   keyed on theme, which is behaviour, not tokens. The dark chart **chrome** (grid,
   labels, legend, tooltip) does switch correctly.
5. **Orange is assigned to no chart series**, per spec — nothing in the app currently
   expresses selection/hover/filter state on a series.
6. **Async-job progress UI** was not added to `UpdateDataByExcelButton`,
   `commentbyExcelButton`, `archive/**/*ByExcel*` or `myComponents/modelForm*` — reserved
   for the follow-up agent. Only their triggers and class-level styling changed.
7. **MUI `class="grid"` / `class="my-5"` leftovers in the `clients2` dialogs** were left
   as-is: converting them to `className` would newly apply `display:grid` and change those
   layouts.
8. **Horizontal overflow on wide tables** (`/archive` 1492 px, `/user_collection` 1789 px
   in a 1104 px container) is pre-existing and *is* scrollable (`overflow-x-auto`), the
   same as `/clients`. Making cells `whitespace-nowrap` to hold 44 px rows everywhere
   would widen it further; only headers were nowrapped.
9. **`Maximum update depth exceeded` on `/clients`** is pre-existing: `ClientsTable`'s
   `useEffect(… , [data])` calls `setSelectedRows([])` while another effect dispatches
   `setSelectedRow(selectedRows)`. Confirmed byte-identical in the pre-Phase-A backup. It
   is a state bug, not a styling one, so it was left for a logic pass.
10. **The screenshot tool's `16_client_detail.png` is really `/clients`** — the detail
    link is `target="_blank"`, so `page.click()` opens a second tab the tool never
    switches to. Phase A had the same artefact. The real client-detail capture was taken
    separately and copied over.

---

## 13. Notes for `src/components/ui/**` (Phase A's files — not touched)

1. **`Table` / `_tables.css`** pins `th`/`td` to `text-left rtl:text-right` at specificity
   (0,1,3), so every numeric column in the app has to use `!text-end` to win. A
   `.table-num` helper class, or an `align="end"` prop on `Th`/`Td`, would delete every
   `!important` in the data tables.
2. **`Button`**: no first-class `destructive` variant — the brief's
   `variant="default" + !text-red-600` works but needs `!important` at ~15 call sites.
   `variant="twoTone"` still renders tinted fills the enterprise register has no slot for
   (~30 call sites); aliasing it to `default` would converge them without edits.
   On **dark theme the navy primary nearly disappears** into `#060820` — `tokens.md` says
   primary becomes azure `#2F5FD6` on dark, and `getBtnColor` does not do that yet.
3. **`Alert`**: `duration` defaults to 3000 and `useTimeout` fires even with no
   `onClose`, so a persistent banner must pass `duration={0}`. Suggest defaulting to
   persistent when `onClose` is undefined. `alertClass` also applies `font-semibold`
   (the fonts ship 400/800 only) and a physical `ltr:ml-2 rtl:mr-2` icon gap.
4. **`Timeline`**: `.timeline-item-content` uses physical `ml-4 rtl:mr-4` and a fixed
   `pb-6`, which call sites must fight to reach 44 px rows. A `separated` variant that
   owns the hairline and hover would be reusable.
5. **`Card`**: `bordered` and `card-shadow` now resolve to the same 1 px border, so the
   prop is a no-op that still invites consumers to reason about shadows. A
   `title` / `subtitle` pair (or a `CardTitle`) would stop every chart and widget card
   re-implementing `text-[15px] font-extrabold text-navy-600` + a 12.5 px muted sub-line.
6. **`Badge`**: needs a token-aware `color` prop. `innerClass="bg-viz-3"` is the only way
   to put a legend swatch on the viz ramp, which is exactly how the `bg-emerald-500`
   drift in `TaskOverview` happened.
7. **`Loading type="cover"`** should offer a `#F2F6F8` skeleton at the content's real
   dimensions instead of a spinner — `dataviz.md` requires the layout not to jump, and
   `TaskOverview` currently props the height up with a bare `<div className="h-[300px]" />`.
8. **`Pagination`'s `Total`** renders English `Total N Items`; `DataTable` now bypasses it
   with its own Arabic range label. Worth retiring or localising.
9. **Copy, for whoever owns i18n:** `ar.json` → `login_prompt` still reads
   `"الرجاء تسجيل اسم المستخدم وكلمة المرور !!"`. The brief forbids editing i18n strings,
   so the two exclamation marks on the sign-in card survive — they are the last ones in
   product chrome (`مرحباً, X!` on the dashboard and the `AccessDenied` copy were
   hard-coded and have been fixed).
10. **`src/locales/lang/{ar,en}.json`**: `NoNotifications` and `plzTryAgainLater` are
   referenced by the notification empty state and **exist in neither file**, so the raw
   keys were rendering. i18next `defaultValue`s were passed as a stopgap; adding the real
   keys makes them inert. (No existing key or string was changed anywhere in Phase B.)

---

## 14. Screenshots

`logs/screens/phaseB/` — `00_sign-in`, `01_home` … `15_supervisor`,
`16_client_detail` (captured separately, see §12.10), `17_settings_archiveReasons`,
plus `_dark_home` and `_dark_clients`. Compare against `logs/screens/phaseA/` and
`logs/screens/before/`.
