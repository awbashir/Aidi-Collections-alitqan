import ModeSwitcher from './ModeSwitcher'
import DirectionSwitcher from './DirectionSwitcher'

export type ThemeConfiguratorProps = {
    callBackClose?: () => void
}

/**
 * Display preferences. The Elstar demo tools (theme-colour picker, layout
 * switcher, nav-mode switcher, "copy config") are removed — Aidi Enterprise
 * has one theme, one layout and one nav mode.
 */
const ThemeConfigurator = ({ callBackClose }: ThemeConfiguratorProps) => {
    return (
        <div className="flex flex-col gap-y-6">
            <div className="flex items-center justify-between gap-4">
                <div>
                    <h6>الوضع الداكن</h6>
                    <span className="text-[12.5px] text-gray-600 dark:text-night-muted">
                        تبديل الواجهة إلى الوضع الداكن
                    </span>
                </div>
                <ModeSwitcher />
            </div>
            <div className="flex items-center justify-between gap-4">
                <div>
                    <h6>اتجاه الواجهة</h6>
                    <span className="text-[12.5px] text-gray-600 dark:text-night-muted">
                        اختر اتجاه العرض
                    </span>
                </div>
                <DirectionSwitcher callBackClose={callBackClose} />
            </div>
        </div>
    )
}

export default ThemeConfigurator
