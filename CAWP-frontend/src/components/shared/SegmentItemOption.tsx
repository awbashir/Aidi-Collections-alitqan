import { forwardRef } from 'react'
import classNames from 'classnames'
import { HiCheckCircle } from 'react-icons/hi'
import type { CommonProps } from '@/@types/common'

interface SegmentItemOptionProps extends CommonProps {
    active: boolean
    customCheck?: string | React.ReactNode
    defaultGutter?: boolean
    disabled?: boolean
    hoverable?: boolean
    onSegmentItemClick?: (
        event: React.MouseEvent<HTMLDivElement, MouseEvent>
    ) => void
}

const SegmentItemOption = forwardRef<HTMLDivElement, SegmentItemOptionProps>(
    (props, ref) => {
        const {
            active,
            children,
            className,
            customCheck,
            defaultGutter = true,
            disabled,
            hoverable,
            onSegmentItemClick,
        } = props

        return (
            <div
                ref={ref}
                className={classNames(
                    'flex',
                    !customCheck && 'justify-between',
                    'items-center',
                    'border',
                    'rounded-md ',
                    'border-gray-300 dark:border-night-border',
                    defaultGutter && 'py-5 px-4',
                    'cursor-pointer',
                    'select-none',
                    'w-100',
                    /* Selection is azure — the same language the table row
                       and the widget frame speak. */
                    active && 'ring-1 ring-azure-500 border-azure-500',
                    hoverable &&
                        'hover:ring-1 hover:ring-azure-500 hover:border-azure-500',
                    disabled && 'opacity-50 cursor-not-allowed',
                    className
                )}
                onClick={onSegmentItemClick}
            >
                {children}
                {active && !customCheck && (
                    <HiCheckCircle className="text-azure-600 text-xl" />
                )}
                {active && customCheck}
            </div>
        )
    }
)

SegmentItemOption.displayName = 'SegmentItemOption'

export default SegmentItemOption
