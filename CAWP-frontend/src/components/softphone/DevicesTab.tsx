/**
 * Audio devices: microphone / speaker / ringer, a microphone level meter, a test tone and a
 * ringer test, and auto-answer for attempt-tagged calls. Saved per user through the API
 * (`PUT /v2/me/calling/preferences`, revision-checked), not in browser storage.
 */
import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { BellRing, Mic, Volume2 } from 'lucide-react'
import { isV2Error, newIdempotencyKey } from '@/services/v2/http'
import { canSelectOutput, listAudioDevices, playTestTone } from '@/services/softphone'
import type { AudioDevice, CallingDevices, SoftphoneEngine } from '@/services/softphone'
import { btnPrimary, btnSecondary } from './LineCard'

const selectClass =
    'h-9 w-full rounded-md border border-slate-300 bg-white px-2 text-[13.5px] focus:border-azure-500 focus:outline-none focus:ring-2 focus:ring-azure-500/20 disabled:opacity-60 dark:border-night-border dark:bg-night-card'

const DeviceSelect = ({
    id,
    label,
    value,
    options,
    disabled,
    onChange,
}: {
    id: string
    label: string
    value: string | null
    options: AudioDevice[]
    disabled?: boolean
    onChange: (v: string | null) => void
}) => {
    const { t } = useTranslation()
    const known = !value || options.some((o) => o.deviceId === value)
    return (
        <label className="block" htmlFor={id}>
            <span className="mb-1 block text-[12.5px] font-extrabold text-navy-600 dark:text-night-strong">{label}</span>
            <select
                id={id}
                data-testid={id}
                className={selectClass}
                value={value || ''}
                disabled={disabled}
                onChange={(e) => onChange(e.target.value || null)}
            >
                <option value="">{t('sp.devices.systemDefault')}</option>
                {!known && <option value={value || ''}>{t('sp.devices.unavailable')}</option>}
                {options.map((o) => (
                    <option key={o.deviceId} value={o.deviceId}>
                        {o.label}
                    </option>
                ))}
            </select>
        </label>
    )
}

const DevicesTab = ({ engine, editable }: { engine: SoftphoneEngine; editable: boolean }) => {
    const { t } = useTranslation()
    const [inputs, setInputs] = useState<AudioDevice[]>([])
    const [outputs, setOutputs] = useState<AudioDevice[]>([])
    const [devices, setDevices] = useState<CallingDevices>({ ...engine.devices })
    const [autoAnswer, setAutoAnswer] = useState<boolean>(engine.getSnapshot().autoAnswerEnabled)
    const [level, setLevel] = useState(0)
    const [metering, setMetering] = useState(false)
    const [saving, setSaving] = useState(false)
    const [message, setMessage] = useState<{ tone: 'ok' | 'error'; text: string } | null>(null)
    const meter = useRef(engine.newMicMeter())
    const saveKey = useRef<string | null>(null)
    const outputSelectable = canSelectOutput()

    useEffect(() => {
        let alive = true
        void listAudioDevices().then((d) => {
            if (!alive) return
            setInputs(d.inputs)
            setOutputs(d.outputs)
        })
        void engine.reloadPreferences().then((p) => {
            if (!alive) return
            setDevices({ ...p.devices })
            setAutoAnswer(p.autoAnswer)
        })
        const m = meter.current
        return () => {
            alive = false
            m.stop()
        }
    }, [engine])

    const change = (patch: Partial<CallingDevices>) => {
        setDevices((d) => ({ ...d, ...patch }))
        saveKey.current = null // a different request: a new key
        setMessage(null)
    }

    const toggleMeter = async () => {
        if (metering) {
            meter.current.stop()
            setMetering(false)
            setLevel(0)
            return
        }
        try {
            await meter.current.start(devices.microphone, setLevel)
            setMetering(true)
        } catch {
            setMessage({ tone: 'error', text: t('sp.devices.micFailed') })
        }
    }

    const save = async () => {
        setSaving(true)
        setMessage(null)
        if (!saveKey.current) saveKey.current = newIdempotencyKey()
        try {
            const res = await engine.saveDevices(devices, autoAnswer, saveKey.current)
            saveKey.current = null
            setMessage({ tone: 'ok', text: t('sp.devices.saved', { revision: res.preferences.revision }) })
        } catch (e) {
            if (isV2Error(e) && e.code === 'revision_conflict') {
                const p = await engine.reloadPreferences()
                setDevices({ ...p.devices })
                setAutoAnswer(p.autoAnswer)
                saveKey.current = null
                setMessage({ tone: 'error', text: t('sp.devices.conflict') })
            } else {
                setMessage({ tone: 'error', text: t('sp.devices.saveFailed') })
            }
        } finally {
            setSaving(false)
        }
    }

    const pct = Math.round(level * 100)
    return (
        <div className="flex flex-col gap-4" data-testid="sp-devices">
            {!editable && <div className="text-[12.5px] text-gray-600">{t('sp.devices.followerNote')}</div>}
            <DeviceSelect
                id="sp-device-microphone"
                label={t('sp.devices.microphone')}
                value={devices.microphone}
                options={inputs}
                disabled={!editable}
                onChange={(v) => change({ microphone: v })}
            />
            <div>
                <div className="mb-1 flex items-center justify-between">
                    <span className="text-[12.5px] text-gray-600">{t('sp.devices.level')}</span>
                    <button type="button" data-testid="sp-mic-test" className={btnSecondary} onClick={() => void toggleMeter()}>
                        <Mic size={15} aria-hidden />
                        {metering ? t('sp.devices.stopMeter') : t('sp.devices.startMeter')}
                    </button>
                </div>
                <div
                    role="meter"
                    aria-label={t('sp.devices.level') as string}
                    aria-valuemin={0}
                    aria-valuemax={100}
                    aria-valuenow={pct}
                    data-testid="sp-mic-level"
                    className="h-2 w-full overflow-hidden rounded-full bg-slate-100 dark:bg-night-raised"
                >
                    <div className="h-full bg-azure-500 transition-[width] duration-100" style={{ width: `${pct}%` }} />
                </div>
            </div>
            <DeviceSelect
                id="sp-device-speaker"
                label={t('sp.devices.speaker')}
                value={devices.speaker}
                options={outputs}
                disabled={!editable || !outputSelectable}
                onChange={(v) => change({ speaker: v })}
            />
            <DeviceSelect
                id="sp-device-ringer"
                label={t('sp.devices.ringer')}
                value={devices.ringer}
                options={outputs}
                disabled={!editable || !outputSelectable}
                onChange={(v) => change({ ringer: v })}
            />
            {!outputSelectable && <div className="text-[12px] text-gray-500">{t('sp.devices.noOutputSelection')}</div>}
            <div className="flex flex-wrap gap-2">
                <button type="button" data-testid="sp-test-tone" className={btnSecondary} onClick={() => void playTestTone(devices.speaker)}>
                    <Volume2 size={15} aria-hidden />
                    {t('sp.devices.testTone')}
                </button>
                <button
                    type="button"
                    data-testid="sp-test-ringer"
                    className={btnSecondary}
                    onClick={() => void playTestTone(devices.ringer, 1500, 480)}
                >
                    <BellRing size={15} aria-hidden />
                    {t('sp.devices.testRinger')}
                </button>
            </div>
            <div className="flex items-start gap-2 text-[13px]">
                <input
                    id="sp-auto-answer"
                    type="checkbox"
                    data-testid="sp-auto-answer"
                    className="mt-0.5 h-4 w-4 accent-navy-600"
                    checked={autoAnswer}
                    disabled={!editable}
                    onChange={(e) => {
                        setAutoAnswer(e.target.checked)
                        saveKey.current = null
                    }}
                />
                <div>
                    <label htmlFor="sp-auto-answer" className="font-extrabold text-navy-600 dark:text-night-strong">
                        {t('sp.devices.autoAnswer')}
                    </label>
                    <div className="text-[12px] text-gray-600">{t('sp.devices.autoAnswerHint')}</div>
                </div>
            </div>
            <div className="flex items-center gap-3">
                <button type="button" data-testid="sp-devices-save" className={btnPrimary} disabled={!editable || saving} onClick={() => void save()}>
                    {t('sp.devices.save')}
                </button>
                {message && (
                    <span
                        data-testid="sp-devices-message"
                        role="status"
                        className={message.tone === 'ok' ? 'text-[12.5px] text-success' : 'text-[12.5px] text-danger'}
                    >
                        {message.text}
                    </span>
                )}
            </div>
            <div className="text-[11.5px] text-gray-500">{t('sp.devices.storedNote')}</div>
        </div>
    )
}

export default DevicesTab
