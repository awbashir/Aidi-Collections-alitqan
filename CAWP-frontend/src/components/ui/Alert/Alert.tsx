import { useState, forwardRef } from 'react'
import classNames from 'classnames'
import useTimeout from '../hooks/useTimeout'
import {
    HiCheckCircle,
    HiInformationCircle,
    HiExclamation,
    HiXCircle,
} from 'react-icons/hi'
import { motion } from 'framer-motion'
import CloseButton from '../CloseButton'
import StatusIcon from '../StatusIcon'
import type { TypeAttributes, CommonProps } from '../@types/common'
import type { ReactNode, MouseEvent } from 'react'

export interface AlertProps extends CommonProps {
    closable?: boolean
    customClose?: ReactNode | string
    customIcon?: ReactNode | string
    duration?: number
    title?: ReactNode | string
    onClose?: (e?: MouseEvent<HTMLDivElement>) => void
    rounded?: boolean
    showIcon?: boolean
    triggerByToast?: boolean
    type?: TypeAttributes.Status
}

const DEFAULT_TYPE = 'warning'

/* Status colours are darkened until they clear 4.5:1 as text on white; the
 * tints are fill-only. Dark-theme values lift to hold contrast on navy ink. */
const TYPE_MAP = {
    success: {
        backgroundColor: 'bg-success-tint dark:bg-success/[.16]',
        titleColor: 'text-success dark:text-[#3FBF8F]',
        textColor: 'text-success dark:text-[#3FBF8F]',
        iconColor: 'text-success dark:text-[#3FBF8F]',
        icon: <HiCheckCircle />,
    },
    info: {
        backgroundColor: 'bg-info-tint dark:bg-info/[.20]',
        titleColor: 'text-info dark:text-azure-300',
        textColor: 'text-info dark:text-azure-300',
        iconColor: 'text-info dark:text-azure-300',
        icon: <HiInformationCircle />,
    },
    warning: {
        backgroundColor: 'bg-warning-tint dark:bg-warning/[.18]',
        titleColor: 'text-warning dark:text-[#E29A55]',
        textColor: 'text-warning dark:text-[#E29A55]',
        iconColor: 'text-warning dark:text-[#E29A55]',
        icon: <HiExclamation />,
    },
    danger: {
        backgroundColor: 'bg-danger-tint dark:bg-danger/[.18]',
        titleColor: 'text-danger dark:text-[#F1786E]',
        textColor: 'text-danger dark:text-[#F1786E]',
        iconColor: 'text-danger dark:text-[#F1786E]',
        icon: <HiXCircle />,
    },
}

const TYPE_ARRAY: TypeAttributes.Status[] = [
    'success',
    'danger',
    'info',
    'warning',
]

const Alert = forwardRef<HTMLDivElement, AlertProps>((props, ref) => {
    const {
        children,
        className,
        closable = false,
        customClose,
        customIcon,
        /* Auto-dismiss is opt-in: a warning or lock banner must persist, and
         * the toast layer drives its own timing through `Notification`. Pass a
         * positive `duration` to get the old 3s behaviour. */
        duration = 0,
        title = null,
        onClose,
        rounded = true,
        showIcon = false,
        triggerByToast = false,
        ...rest
    } = props

    const getType = () => {
        const { type = DEFAULT_TYPE } = props
        if (TYPE_ARRAY.includes(type)) {
            return type
        }
        return DEFAULT_TYPE
    }

    const type = getType()
    const typeMap = TYPE_MAP[type]

    const [display, setDisplay] = useState('show')

    const { clear } = useTimeout(
        onClose as () => void,
        duration,
        (duration as number) > 0
    )

    const handleClose = (e: MouseEvent<HTMLDivElement>) => {
        setDisplay('hiding')
        onClose?.(e)
        clear()
        if (!triggerByToast) {
            setTimeout(() => {
                setDisplay('hide')
            }, 400)
        }
    }

    const renderClose = () => {
        return (
            <div
                className="cursor-pointer"
                role="presentation"
                onClick={(e) => handleClose(e)}
            >
                {customClose || <CloseButton defaultStyle={false} />}
            </div>
        )
    }

    const alertDefaultClass = 'p-4 relative flex'

    const alertClass = classNames(
        'alert',
        alertDefaultClass,
        typeMap.backgroundColor,
        typeMap.textColor,
        !title ? 'font-semibold' : '',
        closable ? 'justify-between' : '',
        closable && !title ? 'items-center' : '',
        rounded && 'rounded-lg',
        className
    )

    if (display === 'hide') {
        return null
    }

    return (
        <motion.div
            ref={ref}
            className={alertClass}
            initial={{ opacity: 1 }}
            animate={display === 'hiding' ? 'exit' : 'animate'}
            transition={{ duration: 0.25, type: 'tween' }}
            variants={{
                animate: {
                    opacity: 1,
                },
                exit: {
                    opacity: 0,
                },
            }}
            {...rest}
        >
            <div className={`flex ${title ? '' : 'items-center'}`}>
                {showIcon && (
                    <StatusIcon
                        iconColor={typeMap.iconColor}
                        custom={customIcon}
                        type={type}
                    />
                )}
                <div className={showIcon ? 'ms-2' : ''}>
                    {title ? (
                        <div
                            className={`font-semibold mb-1 ${typeMap.titleColor}`}
                        >
                            {title}
                        </div>
                    ) : null}
                    {children}
                </div>
            </div>
            {closable ? renderClose() : null}
        </motion.div>
    )
})

Alert.displayName = 'Alert'

export default Alert
