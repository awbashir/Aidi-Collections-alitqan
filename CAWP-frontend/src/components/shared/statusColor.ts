/**
 * Aidi Enterprise — data-driven status colour helper.
 *
 * Statuses in this product carry a colour that comes from the database
 * (`CommentStatuses.color`). The enterprise register never paints a whole row
 * with it; it renders the colour as a 3px rail on the leading edge plus a very
 * light tint, and repeats it as a pill in the status column.
 *
 * `statusTint()` turns any `#rgb` / `#rrggbb` / `rgb()` / `rgba()` string into
 * the three values those three jobs need:
 *
 *   rail  the raw colour, used for the 3px inset rail and the 7px pill dot
 *   tint  the colour mixed 10% into white — legal as a row fill
 *   text  the colour darkened until it clears 4.5:1 as text on white
 */

export type StatusColorSet = {
    /** raw colour — rail, dot */
    rail: string
    /** 10% of the colour over white — row fill */
    tint: string
    /** 20% of the colour over white — pill fill, so a pill still reads as a
     *  pill when it sits on a row already carrying the 10% tint */
    pill: string
    /** darkened to >= 4.5:1 on white — pill label */
    text: string
}

type Rgb = { r: number; g: number; b: number }

const NAMED: Record<string, string> = {
    red: '#B3261E',
    green: '#128A5C',
    blue: '#2F5FD6',
    orange: '#C86A00',
    yellow: '#C86A00',
    teal: '#5B93C9',
    purple: '#8E86B8',
    grey: '#6B7482',
    gray: '#6B7482',
    black: '#2E3450',
    white: '#FFFFFF',
}

const clamp = (n: number) => Math.max(0, Math.min(255, Math.round(n)))

/** Accepts `#rgb`, `#rrggbb`, `rgb(r g b)`, `rgba(r,g,b,a)` and a few names. */
export function parseColor(input?: string | null): Rgb | null {
    if (!input) {
        return null
    }
    let value = String(input).trim().toLowerCase()

    if (NAMED[value]) {
        value = NAMED[value]
    }

    const short = /^#([a-f\d])([a-f\d])([a-f\d])$/i.exec(value)
    if (short) {
        return {
            r: parseInt(short[1] + short[1], 16),
            g: parseInt(short[2] + short[2], 16),
            b: parseInt(short[3] + short[3], 16),
        }
    }

    const long = /^#([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})?$/i.exec(
        value
    )
    if (long) {
        return {
            r: parseInt(long[1], 16),
            g: parseInt(long[2], 16),
            b: parseInt(long[3], 16),
        }
    }

    const fn = /^rgba?\(([^)]+)\)$/i.exec(value)
    if (fn) {
        const parts = fn[1]
            .split(/[\s,/]+/)
            .filter(Boolean)
            .map((p) => parseFloat(p))
        if (parts.length >= 3 && parts.every((p) => !isNaN(p))) {
            return { r: clamp(parts[0]), g: clamp(parts[1]), b: clamp(parts[2]) }
        }
    }

    return null
}

const toHex = ({ r, g, b }: Rgb) =>
    '#' +
    [r, g, b]
        .map((c) => clamp(c).toString(16).padStart(2, '0'))
        .join('')
        .toUpperCase()

/** sRGB relative luminance, per WCAG 2.1. */
function luminance({ r, g, b }: Rgb): number {
    const channel = (c: number) => {
        const s = c / 255
        return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4)
    }
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)
}

/** Contrast ratio against white. */
export function contrastOnWhite(rgb: Rgb): number {
    return 1.05 / (luminance(rgb) + 0.05)
}

/** Mix `rgb` into `base` at `ratio` (0 = base, 1 = the colour). */
function mix(rgb: Rgb, base: Rgb, ratio: number): Rgb {
    return {
        r: base.r + (rgb.r - base.r) * ratio,
        g: base.g + (rgb.g - base.g) * ratio,
        b: base.b + (rgb.b - base.b) * ratio,
    }
}

const WHITE: Rgb = { r: 255, g: 255, b: 255 }
/** `#0C1033` — the dark-theme card surface. */
const NIGHT_CARD: Rgb = { r: 12, g: 16, b: 51 }

/** Contrast ratio between two colours. */
function contrast(a: Rgb, b: Rgb): number {
    const la = luminance(a)
    const lb = luminance(b)
    return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05)
}

/** Walk the colour toward white until it clears `target`:1 on the dark card. */
function lightenToContrast(rgb: Rgb, target = 4.5): Rgb {
    let out = rgb
    for (let i = 0; i < 24 && contrast(out, NIGHT_CARD) < target; i++) {
        out = mix(out, WHITE, 0.88)
    }
    return { r: clamp(out.r), g: clamp(out.g), b: clamp(out.b) }
}

/** Walk the colour toward black until it clears `target`:1 on white. */
function darkenToContrast(rgb: Rgb, target = 4.5): Rgb {
    let out = rgb
    let factor = 1
    // 24 steps of 4% bottoms out near black, which always clears 4.5:1.
    for (let i = 0; i < 24 && contrastOnWhite(out) < target; i++) {
        factor *= 0.96
        out = { r: rgb.r * factor, g: rgb.g * factor, b: rgb.b * factor }
    }
    return {
        r: clamp(out.r),
        g: clamp(out.g),
        b: clamp(out.b),
    }
}

const FALLBACK: StatusColorSet = {
    rail: '#8A929F',
    tint: '#F2F6F8',
    pill: '#E7EEF2',
    text: '#2E3450',
}

const FALLBACK_DARK: StatusColorSet = {
    rail: '#8D95B8',
    tint: '#141A45',
    pill: '#1E2554',
    text: '#C7CDE4',
}

const cache = new Map<string, StatusColorSet>()

/**
 * Turn a DB status colour into an enterprise-safe { rail, tint, text } set.
 *
 * Light: tint = 10% of the colour over white, text = darkened to >= 4.5:1.
 * Dark:  tint = 22% of the colour over the `#0C1033` card, rail and text
 *        lifted until they clear 4.5:1 against that card — a 10%-over-white
 *        tint on a dark page reads as a rendering bug.
 *
 * Returns a neutral set when the input cannot be parsed.
 */
export function statusTint(
    color?: string | null,
    scheme: 'light' | 'dark' = 'light'
): StatusColorSet {
    const dark = scheme === 'dark'
    if (!color) {
        return dark ? FALLBACK_DARK : FALLBACK
    }
    const key = scheme + '|' + String(color)
    const hit = cache.get(key)
    if (hit) {
        return hit
    }

    const rgb = parseColor(String(color))
    if (!rgb) {
        const miss = dark ? FALLBACK_DARK : FALLBACK
        cache.set(key, miss)
        return miss
    }

    const set: StatusColorSet = dark
        ? {
              rail: toHex(lightenToContrast(rgb, 3)),
              tint: toHex(mix(rgb, NIGHT_CARD, 0.22)),
              pill: toHex(mix(rgb, NIGHT_CARD, 0.38)),
              text: toHex(lightenToContrast(rgb)),
          }
        : {
              rail: toHex(rgb),
              tint: toHex(mix(rgb, WHITE, 0.1)),
              pill: toHex(mix(rgb, WHITE, 0.2)),
              text: toHex(darkenToContrast(rgb)),
          }
    cache.set(key, set)
    return set
}

/** `true` when the colour is light enough to need dark text on top of it. */
export function isLightColor(color?: string | null): boolean {
    const rgb = parseColor(color)
    if (!rgb) {
        return true
    }
    return luminance(rgb) > 0.4
}

export default statusTint
