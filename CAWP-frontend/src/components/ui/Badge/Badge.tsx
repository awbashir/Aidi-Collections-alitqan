import { forwardRef } from 'react'
import classNames from 'classnames'
import type { CommonProps } from '../@types/common'
import type { CSSProperties } from 'react'

export interface BadgeProps extends CommonProps {
    badgeStyle?: CSSProperties
    /** A literal colour (hex / rgb / a CSS name). `.badge-dot` carries the
     *  orange notification fill and its `.dark` variant outranks any utility
     *  class, so a caller that needs a specific colour — a chart-legend swatch,
     *  a DB-driven status dot — sets it here rather than through `innerClass`. */
    color?: string
    content?: string | number
    innerClass?: string
    maxCount?: number
}

const Badge = forwardRef<HTMLElement, BadgeProps>((props, ref) => {
    const {
        badgeStyle,
        children,
        className,
        color,
        content,
        innerClass,
        maxCount = 99,
        ...rest
    } = props

    const style: CSSProperties | undefined = color
        ? { backgroundColor: color, ...badgeStyle }
        : badgeStyle

    const dot = typeof content !== 'number' && typeof content !== 'string'

    const badgeClass = classNames(dot ? 'badge-dot' : 'badge', innerClass)

    const renderBadge = () => {
        if (children) {
            return (
                <span
                    ref={ref}
                    className={classNames('badge-wrapper', className)}
                    {...rest}
                >
                    <span
                        className={classNames(badgeClass, 'badge-inner')}
                        style={style}
                    >
                        {typeof content === 'number' && content > maxCount
                            ? `${maxCount}+`
                            : content}
                    </span>
                    {children}
                </span>
            )
        }
        return (
            <span
                ref={ref}
                className={classNames(badgeClass, className)}
                style={style}
                {...rest}
            >
                {content}
            </span>
        )
    }

    return renderBadge()
})

Badge.displayName = 'Badge'

export default Badge
