# Aidi Enterprise — Data visualisation

---

## Categorical palette

Eight hues walking navy → azure → steel → sky → lavender → slate. The ramp is deliberately
**close in hue** so that orange, held out of the set, reads instantly as the emphasised series.

| # | Light | Dark |
| --- | --- | --- |
| 01 | `#070A4B` | `#7D9DEA` |
| 02 | `#2F5FD6` | `#5482E5` |
| 03 | `#5B93C9` | `#8FB9CE` |
| 04 | `#8FB9CE` | `#B2AAC4` |
| 05 | `#8E86B8` | `#8E86B8` |
| 06 | `#B2AAC4` | `#B3C8F4` |
| 07 | `#6B7482` | `#8D95B8` |
| 08 | `#B8C4CE` | `#565CA6` |
| highlight | `#FF6600` | `#FF8433` |

**Never assign orange to a category.** Assign it to whatever the user selected, hovered, or
filtered to. It is a state, not an identity.

Beyond eight series, group the tail into "Other" in `#B8C4CE` rather than extending the ramp.
Nobody reads a nine-colour legend.

## Continuous ramps

```
Sequential — one measure, low → high
  #EDF3F8 → #B9D2E6 → #7FA9D2 → #4A7BC0 → #2450A0 → #070A4B

Diverging — variance around a target
  #B25A00 → #E8A66A → #F2F6F8 → #8FB9CE → #2450A0
```

The diverging ramp's warm end reuses the warning colour deliberately: below-target reads as
warm, above-target as cool, and the midpoint is the neutral `#F2F6F8`. Anchor the midpoint on
the target, not on the data's mean.

---

## Chart chrome

| Part | Spec |
| --- | --- |
| Chart card | radius 8px, 1px `#D7E1E8`, padding 20px, no shadow |
| Title | 15px / 800 `#070A4B`; sub-line 12.5px `#6B7482` tabular |
| Grid lines | 1px `#E7EEF2`, horizontal only. No vertical grid on a time series. |
| Axis line | 1px `#D7E1E8`; ticks `#8A929F` |
| Axis labels | 11.5px `#6B7482`, tabular |
| Series label | 12px / 800, in the series colour, placed at the line end — prefer this over a legend |
| Legend | 12px `#2E3450`, 8px swatch at radius 2px, 16px gap, above the plot |
| Line weight | 2px; emphasised series 2.5px |
| Bar gap | 30% of band width; grouped-bar inner gap 4px |
| Zero line | 1px `#B8C4CE` — heavier than a grid line, always drawn when values cross zero |
| Tooltip | bg `#070A4B`, text `#fff` 12.5px tabular, radius 6px, padding `8px 10px`, shadow `0 8px 24px rgba(7,10,75,.10)` |
| Target / benchmark | 1px dashed `#8A929F` with an inline 11px `#6B7482` label |
| Forecast / projected | same hue, `stroke-dasharray: 4 3`, 60% opacity |

Axis labels and every tooltip number use `font-variant-numeric: tabular-nums`.

---

## Rules

**Y axes start at zero for bars.** Line and area charts may truncate, but the axis must then
be labelled and the truncation visible — never a silent break.

**One measure per axis.** A dual-axis chart implies a correlation the data may not support.
If two measures must sit together, use two stacked panels sharing an x axis.

**Annotate rather than decorate.** A callout on the one point that matters beats a legend of
eight. Enterprise users are reading for a decision, not browsing.

**Null is not zero.** Break the line, and note the gap in the sub-line. A gap silently drawn
as zero is a data-integrity bug rendered as a chart.

**Show the denominator.** A percentage without its base ("+18%") is unreadable in a governed
product; write "+18% (1,204 → 1,421)".

**Empty state** carries the reason, not a shrug: "No runs in this period" plus the filter that
produced it and a tertiary button to clear it.

**Loading** is a skeleton in `#F2F6F8` at the chart's real dimensions, not a spinner — the
layout must not jump when data arrives.

---

## RTL

- **The time axis does not mirror.** Oldest stays left; the axis is numeric.
- Category axes on horizontal bar charts **do** mirror — bars grow from the inline-start edge.
- Legends, titles, tooltips and annotations mirror with the layout.
- Numbers stay Western-Arabic and tabular in both directions (see `rtl-arabic.md`).
- Put the unit or currency in the axis title, not on every tick, so it needs no flip.

---

## Accessibility

Adjacent categorical hues in the ramp are close by design, so **colour alone never carries
meaning**:

- Line charts: pair colour with dash pattern or an end-of-line label.
- Bar charts: order the bars, and label the emphasised one directly.
- Status in charts inherits the badge glyph shapes — dot, diamond, bar, ring.
- Text on any tint clears 4.5:1; text on `#8FB9CE` and lighter must be `#070A4B`.
- Every chart has a table equivalent one click away. The audit log is the source of truth;
  the chart is a view of it.
