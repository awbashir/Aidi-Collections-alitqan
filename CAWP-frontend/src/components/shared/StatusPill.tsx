import classNames from 'classnames'
import { useConfig } from '@/components/ui/ConfigProvider'
import { statusTint } from './statusColor'
import type { CSSProperties, ReactNode } from 'react'

/**
 * Aidi Enterprise status pill.
 *
 * Radius 999, 24px tall (22px inside a table row), 12px / 800, tint fill,
 * darkened text. Every tone carries a distinct **glyph shape** as well as a
 * colour so the set survives greyscale printing and colour-vision deficiency:
 *
 *   success  7px filled circle
 *   warning  7px square rotated 45° (diamond)
 *   danger   8 × 2px bar
 *   info     7px circle, 2px ring, hollow
 *   neutral  7px circle in #8A929F
 *
 * Pass `color` to drive the pill from a database colour instead of a tone —
 * the glyph stays a filled dot in the raw colour, the fill is a 10% tint and
 * the label is the colour darkened to >= 4.5:1 on white.
 */

export type StatusTone = 'success' | 'warning' | 'danger' | 'info' | 'neutral'

export interface StatusPillProps {
    tone?: StatusTone
    /** Raw DB colour (`#rrggbb` / `rgb()`). Overrides `tone`. */
    color?: string | null
    children?: ReactNode
    className?: string
    /** `row` = 22px for use inside a 44px table row. `md` = 24px. */
    size?: 'row' | 'md'
    title?: string
}

const TONE_CLASS: Record<StatusTone, string> = {
    success: 'tag-success',
    warning: 'tag-warning',
    danger: 'tag-danger',
    info: 'tag-info',
    neutral: 'tag-neutral',
}

const Glyph = ({ tone, color }: { tone: StatusTone; color?: string }) => {
    const style: CSSProperties | undefined = color
        ? { backgroundColor: color, borderColor: color }
        : undefined

    switch (tone) {
        case 'warning':
            return (
                <span
                    aria-hidden
                    className="inline-block h-[7px] w-[7px] rotate-45 bg-current shrink-0"
                    style={style}
                />
            )
        case 'danger':
            return (
                <span
                    aria-hidden
                    className="inline-block h-[2px] w-[8px] rounded-none bg-current shrink-0"
                    style={style}
                />
            )
        case 'info':
            return (
                <span
                    aria-hidden
                    className="inline-block h-[7px] w-[7px] rounded-full border-2 border-current bg-transparent shrink-0"
                    style={color ? { borderColor: color } : undefined}
                />
            )
        case 'neutral':
            return (
                <span
                    aria-hidden
                    className="inline-block h-[7px] w-[7px] rounded-full shrink-0 bg-gray-500"
                    style={style}
                />
            )
        default:
            return (
                <span
                    aria-hidden
                    className="inline-block h-[7px] w-[7px] rounded-full bg-current shrink-0"
                    style={style}
                />
            )
    }
}

const StatusPill = (props: StatusPillProps) => {
    const {
        tone = 'neutral',
        color,
        children,
        className,
        size = 'row',
        title,
    } = props

    const { mode } = useConfig()
    const derived = color
        ? statusTint(color, mode === 'dark' ? 'dark' : 'light')
        : null

    const style: CSSProperties = {
        height: size === 'row' ? 22 : 24,
        ...(derived
            ? { backgroundColor: derived.pill, color: derived.text }
            : {}),
    }

    return (
        <span
            title={title}
            style={style}
            className={classNames(
                'tag',
                !derived && TONE_CLASS[tone],
                'max-w-full',
                className
            )}
        >
            <Glyph tone={tone} color={derived ? derived.rail : undefined} />
            <span className="truncate">{children}</span>
        </span>
    )
}

export default StatusPill
