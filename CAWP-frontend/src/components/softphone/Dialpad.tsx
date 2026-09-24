/**
 * The idle dialpad: internal extensions only (the server's pattern). An external number is
 * never dialled from here: it is refused with a pointer to click-to-call on a case contact.
 */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Phone } from 'lucide-react'
import { DTMF_KEYS, isInternalTarget } from '@/services/softphone'
import type { SoftphoneEngine } from '@/services/softphone'
import { btnPrimary, btnSecondary } from './LineCard'

const Dialpad = ({ engine, pattern, enabled }: { engine: SoftphoneEngine; pattern: string | null; enabled: boolean }) => {
    const { t } = useTranslation()
    const [value, setValue] = useState('')
    const [error, setError] = useState<string | null>(null)
    const internal = isInternalTarget(value, pattern)
    const looksExternal = value.length >= 7
    const call = async () => {
        setError(null)
        try {
            await engine.callInternal(value)
            setValue('')
        } catch (e) {
            setError((e as { code?: string }).code || 'error')
        }
    }
    return (
        <section aria-label={t('sp.dial.title') as string} className="rounded-lg border border-slate-200 p-3 dark:border-night-border">
            <div className="text-eyebrow mb-2">{t('sp.dial.title')}</div>
            <form
                className="flex gap-2"
                onSubmit={(e) => {
                    e.preventDefault()
                    if (internal && enabled) void call()
                }}
            >
                <input
                    data-testid="sp-dial-input"
                    dir="ltr"
                    inputMode="numeric"
                    autoComplete="off"
                    value={value}
                    aria-label={t('sp.dial.input') as string}
                    placeholder={t('sp.dial.placeholder') as string}
                    onChange={(e) => {
                        setValue(e.target.value.replace(/[^0-9]/g, '').slice(0, 15))
                        setError(null)
                    }}
                    className="h-9 min-w-0 flex-1 rounded-md border border-slate-300 px-3 text-[14px] tabular-nums focus:border-azure-500 focus:outline-none focus:ring-2 focus:ring-azure-500/20 dark:border-night-border dark:bg-night-card"
                />
                <button type="submit" data-testid="sp-dial-call" disabled={!internal || !enabled} className={btnPrimary}>
                    <Phone size={15} aria-hidden />
                    {t('sp.dial.call')}
                </button>
            </form>
            <div className="mt-2 grid grid-cols-3 gap-1.5">
                {DTMF_KEYS.map((k) => (
                    <button
                        key={k}
                        type="button"
                        tabIndex={-1}
                        className={`${btnSecondary} px-0 tabular-nums`}
                        disabled={k === '*' || k === '#'}
                        onClick={() => setValue((v) => (v + k).slice(0, 15))}
                    >
                        {k}
                    </button>
                ))}
            </div>
            <div data-testid="sp-dial-hint" data-external={looksExternal ? '1' : '0'} className="mt-2 text-[12px] text-gray-600 dark:text-night-body">
                {looksExternal ? t('sp.dial.externalRefused') : t('sp.dial.internalOnly')}
            </div>
            {error && <div className="mt-1 text-[12px] text-danger">{t(`sp.error.${error}`, error)}</div>}
        </section>
    )
}

export default Dialpad
