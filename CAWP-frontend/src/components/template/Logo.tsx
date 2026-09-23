import classNames from 'classnames'
import { APP_NAME, TENANT_NAME } from '@/constants/app.constant'
import type { CommonProps } from '@/@types/common'

interface LogoProps extends CommonProps {
    type?: 'full' | 'streamline'
    mode?: 'light' | 'dark'
    imgClass?: string
    logoWidth?: number | string
    /** Hide the tenant eyebrow (e.g. inside a tight header). */
    hideTenant?: boolean
}

const LOGO_SRC_PATH = '/aidi/'

/**
 * The Aidi lockup. `full` renders the horizontal wordmark plus the tenant
 * eyebrow; `streamline` (collapsed nav) renders the monogram only.
 * The lockup is fixed artwork — it never mirrors under RTL.
 */
const Logo = (props: LogoProps) => {
    const {
        type = 'full',
        mode = 'light',
        className,
        imgClass,
        style,
        logoWidth = 'auto',
        hideTenant = false,
    } = props

    const isStreamline = type === 'streamline'
    const isDark = mode === 'dark'

    const src = isStreamline
        ? `${LOGO_SRC_PATH}aidi-mark-${isDark ? 'white' : 'navy'}.svg`
        : `${LOGO_SRC_PATH}aidi-wordmark-${isDark ? 'white' : 'navy'}.svg`

    return (
        <div
            className={classNames(
                'logo flex items-center gap-2.5',
                isStreamline && 'justify-center',
                className
            )}
            style={{
                ...style,
                ...{ width: logoWidth },
            }}
        >
            <img
                className={classNames('block w-auto shrink-0', imgClass)}
                style={{ height: isStreamline ? 24 : 26 }}
                src={src}
                alt={`${APP_NAME} logo`}
            />
            {!isStreamline && !hideTenant && (
                <>
                    <span
                        className="w-px self-stretch my-2 bg-gray-300 dark:bg-night-border"
                        aria-hidden="true"
                    />
                    <span
                        dir="rtl"
                        className="text-[11px] font-extrabold leading-tight text-gray-500 dark:text-night-muted truncate"
                    >
                        {TENANT_NAME}
                    </span>
                </>
            )}
        </div>
    )
}

export default Logo
