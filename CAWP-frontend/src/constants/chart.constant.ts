/**
 * Aidi AI Enterprise — data-visualisation palette.
 *
 * Eight hues walking navy -> azure -> steel -> sky -> lavender -> slate. The
 * ramp is deliberately close in hue so that orange, held out of the set, reads
 * instantly as the emphasised series.
 *
 * Orange (`COLOR_HIGHLIGHT` / the `viz-highlight` Tailwind token) is a STATE,
 * not an identity: assign it to whatever the user selected, hovered or filtered
 * to. Never assign it to a category.
 *
 * Beyond eight series, group the tail into "Other" in `#B8C4CE` rather than
 * extending the ramp.
 *
 * Values are literal hexes rather than `twin.macro`'s `theme\`colors\`` so the
 * chart palette is readable at the call site and cannot drift when a stock
 * Tailwind hue gets re-mapped in `tailwind.config.cjs`.
 * Mirrors the `viz-{1..8}` / `viz-highlight` Tailwind tokens.
 */

/* ── Categorical · light ─────────────────────────────────────────────── */
export const COLOR_1 = '#070A4B' // navy
export const COLOR_2 = '#2F5FD6' // azure
export const COLOR_3 = '#5B93C9' // steel
export const COLOR_4 = '#8FB9CE' // sky
export const COLOR_5 = '#8E86B8' // lavender
export const COLOR_6 = '#B2AAC4' // lavender tint
export const COLOR_7 = '#6B7482' // slate
export const COLOR_8 = '#B8C4CE' // slate tint / "Other"

/** Emphasis only — selected, hovered or filtered-to series. Never a category. */
export const COLOR_HIGHLIGHT = '#FF6600'
export const COLOR_HIGHLIGHT_DARK = '#FF8433'
export const COLOR_HIGHLIGHT_LIGHT = '#FFE0CC'

/* ── Categorical · light tints (20% of the hue over white) ───────────── */
export const COLOR_1_LIGHT = '#CDCEDB'
export const COLOR_2_LIGHT = '#D5DFF7'
export const COLOR_3_LIGHT = '#DEE9F4'
export const COLOR_4_LIGHT = '#E9F1F5'
export const COLOR_5_LIGHT = '#E8E7F1'
export const COLOR_6_LIGHT = '#F0EEF3'
export const COLOR_7_LIGHT = '#E1E3E6'
export const COLOR_8_LIGHT = '#F1F3F5'

/* ── Categorical · dark theme ────────────────────────────────────────── */
export const COLOR_1_DARK = '#7D9DEA'
export const COLOR_2_DARK = '#5482E5'
export const COLOR_3_DARK = '#8FB9CE'
export const COLOR_4_DARK = '#B2AAC4'
export const COLOR_5_DARK = '#8E86B8'
export const COLOR_6_DARK = '#B3C8F4'
export const COLOR_7_DARK = '#8D95B8'
export const COLOR_8_DARK = '#565CA6'

export const COLORS = [
    COLOR_1,
    COLOR_2,
    COLOR_3,
    COLOR_4,
    COLOR_5,
    COLOR_6,
    COLOR_7,
    COLOR_8,
]

export const COLORS_LIGHT = [
    COLOR_1_LIGHT,
    COLOR_2_LIGHT,
    COLOR_3_LIGHT,
    COLOR_4_LIGHT,
    COLOR_5_LIGHT,
    COLOR_6_LIGHT,
    COLOR_7_LIGHT,
    COLOR_8_LIGHT,
]

export const COLORS_DARK = [
    COLOR_1_DARK,
    COLOR_2_DARK,
    COLOR_3_DARK,
    COLOR_4_DARK,
    COLOR_5_DARK,
    COLOR_6_DARK,
    COLOR_7_DARK,
    COLOR_8_DARK,
]

/* ── Continuous ramps ────────────────────────────────────────────────── */

/** One measure, low -> high. */
export const RAMP_SEQUENTIAL = [
    '#EDF3F8',
    '#B9D2E6',
    '#7FA9D2',
    '#4A7BC0',
    '#2450A0',
    '#070A4B',
]

/** Variance around a target — anchor the midpoint on the target, not the mean. */
export const RAMP_DIVERGING = [
    '#B25A00',
    '#E8A66A',
    '#F2F6F8',
    '#8FB9CE',
    '#2450A0',
]

/* ── Chrome ──────────────────────────────────────────────────────────── */

/** Horizontal grid line. 1px, horizontal only — no vertical grid. */
export const GRID = '#E7EEF2'
export const GRID_DARK = '#1B2049'

/** Axis line. */
export const AXIS = '#D7E1E8'
export const AXIS_DARK = '#262C5E'

/** Axis ticks. */
export const AXIS_TICK = '#8A929F'
export const AXIS_TICK_DARK = '#3A4179'

/** Axis label text — 11.5px, tabular. */
export const LABEL = '#6B7482'
export const LABEL_DARK = '#6E769B'

/** Legend text — 12px. */
export const LEGEND = '#2E3450'
export const LEGEND_DARK = '#C7CDE4'

/** Tooltip fill. Applied in CSS — Apex cannot set it from options. */
export const TOOLTIP_BG = '#070A4B'
export const TOOLTIP_BG_DARK = '#141A45'
export const TOOLTIP_TEXT = '#FFFFFF'
export const TOOLTIP_TEXT_DARK = '#F2F5FB'

/** Heavier than a grid line — always drawn when values cross zero. */
export const ZERO_LINE = '#B8C4CE'

/** Dashed target / benchmark rule. */
export const TARGET_LINE = '#8A929F'

/**
 * Latin glyphs and digits render in Anodina, Arabic falls through to Manifa V2,
 * so numerals stay Western-Arabic and tabular in both directions.
 */
export const CHART_FONT_FAMILY = 'Anodina, "Manifa V2", sans-serif'
