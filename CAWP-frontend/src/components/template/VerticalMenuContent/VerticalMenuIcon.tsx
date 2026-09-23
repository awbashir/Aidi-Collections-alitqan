import navigationIcon from '@/configs/navigation-icon.config'
import type { ElementType, ComponentPropsWithRef } from 'react'

type VerticalMenuIconProps = {
    icon: string
    gutter: string
}

export const Icon = <T extends ElementType>({
    component,
    ...props
}: {
    header: T
} & ComponentPropsWithRef<T>) => {
    const Component = component
    return <Component {...props} />
}

const VerticalMenuIcon = ({ icon, gutter }: VerticalMenuIconProps) => {
    if (typeof icon !== 'string' && !icon) {
        return <></>
    }

    return (
        <span
            className={`menu-item-icon text-xl shrink-0 ${
                gutter ? 'ltr:mr-2.5 rtl:ml-2.5' : ''
            }`}
        >
            {navigationIcon[icon]}
        </span>
    )
}

VerticalMenuIcon.defaultProps = {
    gutter: true,
}

export default VerticalMenuIcon
