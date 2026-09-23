import { forwardRef } from 'react'
import classNames from 'classnames'
import { useConfig } from '../ConfigProvider'
import { useForm } from '../Form/context'
import { useInputGroup } from '../InputGroup/context'
import { CONTROL_SIZES, SIZES } from '../utils/constants'
import { Spinner } from '../Spinner'
import type { CommonProps, TypeAttributes } from '../@types/common'
import type { ReactNode, ComponentPropsWithRef, MouseEvent } from 'react'

export interface ButtonProps
    extends CommonProps,
        Omit<ComponentPropsWithRef<'button'>, 'onClick'> {
    active?: boolean
    block?: boolean
    color?: string
    disabled?: boolean
    icon?: string | ReactNode
    loading?: boolean
    onClick?: (e: MouseEvent<HTMLButtonElement>) => void
    shape?: TypeAttributes.Shape
    size?: TypeAttributes.Size
    variant?: 'solid' | 'twoTone' | 'plain' | 'default' | 'destructive'
}

type ButtonColor = {
    bgColor: string
    hoverColor: string
    activeColor: string
    textColor: string
}

const Button = forwardRef<HTMLButtonElement, ButtonProps>((props, ref) => {
    const {
        active = false,
        block = false,
        children,
        className,
        color = '',
        disabled,
        icon,
        loading = false,
        shape = 'round',
        size,
        variant = 'default',
        ...rest
    } = props
    const { themeColor, controlSize, primaryColorLevel } = useConfig()
    const formControlSize = useForm()?.size
    const inputGroupSize = useInputGroup()?.size
    const defaultClass = 'button'
    const sizeIconClass = 'inline-flex items-center justify-center'

    const splitedColor = color?.split('-') || []

    const buttonSize = size || inputGroupSize || formControlSize || controlSize
    const buttonColor = splitedColor[0] || themeColor
    /* `color="signal"` is the one orange button — max one per screen.
     * Its rest state is 500 (#FF6600), not the theme's 600. */
    const buttonColorLevel =
        splitedColor[1] ||
        (buttonColor === 'signal' ? '500' : primaryColorLevel)

    /* The Aidi scales darken as the level rises, so hover/active step UP a
     * level (navy 600 -> 700 -> 800), unlike the template's default. */
    const shiftLevel = (level: string | number, by: number) => {
        const levels = [
            '50',
            '100',
            '200',
            '300',
            '400',
            '500',
            '600',
            '700',
            '800',
            '900',
        ]
        const index = levels.indexOf(String(level))
        if (index < 0) {
            return String(level)
        }
        return levels[Math.min(levels.length - 1, Math.max(0, index + by))]
    }

    const hoverLevel = shiftLevel(buttonColorLevel, 1)
    const activeLevel = shiftLevel(buttonColorLevel, 2)

    const getButtonSize = () => {
        let sizeClass = ''
        switch (buttonSize) {
            case SIZES.LG:
                sizeClass = classNames(
                    `h-${CONTROL_SIZES.lg}`,
                    icon && !children
                        ? `w-${CONTROL_SIZES.lg} ${sizeIconClass} text-xl`
                        : 'px-5 text-[14px]'
                )
                break
            case SIZES.SM:
                sizeClass = classNames(
                    `h-${CONTROL_SIZES.sm}`,
                    icon && !children
                        ? `w-${CONTROL_SIZES.sm} ${sizeIconClass} text-base`
                        : 'px-3.5 text-[12.5px]'
                )
                break
            case SIZES.XS:
                sizeClass = classNames(
                    `h-${CONTROL_SIZES.xs}`,
                    icon && !children
                        ? `w-${CONTROL_SIZES.xs} ${sizeIconClass} text-sm`
                        : 'px-3 text-[12px]'
                )
                break
            default:
                sizeClass = classNames(
                    `h-${CONTROL_SIZES.md}`,
                    icon && !children
                        ? `w-${CONTROL_SIZES.md} ${sizeIconClass} text-lg`
                        : 'px-[18px]'
                )
                break
        }
        return sizeClass
    }

    const disabledClass = 'opacity-50 cursor-not-allowed'

    /* Primary — navy #070A4B, hover #14184F, active #04062F. Never orange.
     * On dark the navy fill disappears into the #060820 page, so the primary
     * becomes azure #2F5FD6 / #5482E5 / #234BB0 there (tokens.md). Only the
     * theme hue flips: an explicit `color="red-600"` keeps its own ramp. */
    const isPrimaryHue = buttonColor === 'navy' || buttonColor === 'indigo'

    const solidColor = () => {
        const btn = {
            bgColor: classNames(
                active
                    ? `bg-${buttonColor}-${hoverLevel}`
                    : `bg-${buttonColor}-${buttonColorLevel}`,
                isPrimaryHue &&
                    (active ? 'dark:bg-azure-400' : 'dark:bg-azure-500')
            ),
            textColor: 'text-white',
            hoverColor: active
                ? ''
                : classNames(
                      `hover:bg-${buttonColor}-${hoverLevel}`,
                      isPrimaryHue && 'dark:hover:bg-azure-400'
                  ),
            activeColor: classNames(
                `active:bg-${buttonColor}-${activeLevel}`,
                isPrimaryHue && 'dark:active:bg-azure-600'
            ),
        }
        return getBtnColor(btn)
    }

    /* Destructive — #B3261E / #8F1E17 / #6E1712, white label. The one solid
     * red in the system: delete, revoke, block. */
    const destructiveColor = () => {
        const btn = {
            bgColor: active ? 'bg-red-700' : 'bg-red-600',
            textColor: 'text-white',
            hoverColor: active ? '' : 'hover:bg-red-700',
            activeColor: 'active:bg-red-800',
        }
        return getBtnColor(btn)
    }

    const twoToneColor = () => {
        const btn = {
            bgColor: active
                ? `bg-${buttonColor}-200 dark:bg-${buttonColor}-50`
                : `bg-${buttonColor}-50 dark:bg-${buttonColor}-500 dark:bg-opacity-20`,
            textColor: `text-${buttonColor}-${buttonColorLevel} dark:text-${buttonColor}-50`,
            hoverColor: active
                ? ''
                : `hover:bg-${buttonColor}-100 dark:hover:bg-${buttonColor}-500 dark:hover:bg-opacity-30`,
            activeColor: `active:bg-${buttonColor}-200 dark:active:bg-${buttonColor}-500 dark:active:bg-opacity-40`,
        }
        return getBtnColor(btn)
    }

    /* Secondary — white, 1px #B8C4CE, navy label. */
    const defaultColor = () => {
        const btn = {
            bgColor: active
                ? `bg-gray-200 border border-gray-400 dark:bg-night-raised dark:border-night-border-strong`
                : `bg-white border border-gray-400 dark:bg-transparent dark:border-night-border-strong`,
            textColor: `text-navy-600 dark:text-night-body`,
            hoverColor: active
                ? ''
                : `hover:bg-gray-100 dark:hover:bg-night-raised`,
            activeColor: `active:bg-gray-200 dark:active:bg-night-raised`,
        }
        return getBtnColor(btn)
    }

    /* Tertiary — bare azure text on a #EEF3FD hover fill. */
    const plainColor = () => {
        const btn = {
            bgColor: active
                ? `bg-navy-100 dark:bg-night-raised`
                : 'bg-transparent border border-transparent',
            textColor: `text-azure-600 dark:text-azure-300`,
            hoverColor: active
                ? ''
                : `hover:bg-navy-50 dark:hover:bg-night-raised`,
            activeColor: `active:bg-navy-100 dark:active:bg-night-raised`,
        }
        return getBtnColor(btn)
    }

    const getBtnColor = ({
        bgColor,
        hoverColor,
        activeColor,
        textColor,
    }: ButtonColor) => {
        return `${bgColor} ${
            disabled || loading ? disabledClass : hoverColor + ' ' + activeColor
        } ${textColor}`
    }

    const btnColor = () => {
        switch (variant) {
            case 'solid':
                return solidColor()
            case 'twoTone':
                return twoToneColor()
            case 'plain':
                return plainColor()
            case 'destructive':
                return destructiveColor()
            case 'default':
                return defaultColor()
            default:
                return defaultColor()
        }
    }

    const classes = classNames(
        defaultClass,
        btnColor(),
        `radius-${shape}`,
        getButtonSize(),
        className,
        block ? 'w-full' : ''
    )

    const handleClick = (e: MouseEvent<HTMLButtonElement>) => {
        const { onClick } = props
        if (disabled || loading) {
            e.preventDefault()
            return
        }
        onClick?.(e)
    }

    const renderChildren = () => {
        if (loading && children) {
            return (
                <span className="flex items-center justify-center">
                    <Spinner enableTheme={false} className="ltr:mr-2 rtl:ml-2" />
                    {children}
                </span>
            )
        }

        if (icon && !children && loading) {
            return <Spinner enableTheme={false} />
        }

        if (icon && !children && !loading) {
            return <>{icon}</>
        }

        if (icon && children && !loading) {
            return (
                <span className="flex items-center justify-center gap-2">
                    <span className="text-base">{icon}</span>
                    <span>{children}</span>
                </span>
            )
        }

        return <>{children}</>
    }

    return (
        <button ref={ref} className={classes} {...rest} onClick={handleClick}>
            {renderChildren()}
        </button>
    )
})

Button.displayName = 'Button'

export default Button
