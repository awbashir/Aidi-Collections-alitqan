import classNames from 'classnames'
import { Link } from 'react-router-dom'
import type { CommonProps } from '@/@types/common'
import type { ComponentPropsWithoutRef } from 'react'

interface ActionLink extends CommonProps, ComponentPropsWithoutRef<'a'> {
    themeColor?: boolean
    to?: string
    href?: string
    reloadDocument?: boolean
}

const ActionLink = (props: ActionLink) => {
    const {
        children,
        className,
        themeColor = true,
        to,
        reloadDocument,
        href = '',
        ...rest
    } = props

    /* Interactivity is azure in the enterprise register, not the brand navy —
       `.text-link` is #234BB0 with a #1A3A8A hover. */
    const classNameProps = {
        className: classNames(
            themeColor && 'text-link',
            'hover:underline',
            className
        ),
    }

    return to ? (
        <Link
            to={to}
            reloadDocument={reloadDocument}
            {...classNameProps}
            {...rest}
        >
            {children}
        </Link>
    ) : (
        <a href={href} {...classNameProps} {...rest}>
            {children}
        </a>
    )
}

export default ActionLink
