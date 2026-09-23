import {
    HiCheckCircle,
    HiInformationCircle,
    HiExclamation,
    HiXCircle,
} from 'react-icons/hi'
import type { TypeAttributes, CommonProps } from '../@types/common'
import type { ReactNode } from 'react'

export interface StatusIconProps extends CommonProps {
    type: TypeAttributes.Status
    custom?: ReactNode | JSX.Element
    iconColor?: string
}

const ICONS: Record<
    TypeAttributes.Status,
    {
        color: string
        icon: JSX.Element
    }
> = {
    success: {
        color: 'text-success dark:text-[#3FBF8F]',
        icon: <HiCheckCircle />,
    },
    info: {
        color: 'text-info dark:text-azure-300',
        icon: <HiInformationCircle />,
    },
    warning: {
        color: 'text-warning dark:text-[#E29A55]',
        icon: <HiExclamation />,
    },
    danger: {
        color: 'text-danger dark:text-[#F1786E]',
        icon: <HiXCircle />,
    },
}

const StatusIcon = (props: StatusIconProps) => {
    const { type = 'info', custom, iconColor } = props

    const icon = ICONS[type]

    return (
        <span className={`text-xl ${iconColor || icon.color}`}>
            {custom || icon.icon}
        </span>
    )
}

export default StatusIcon
