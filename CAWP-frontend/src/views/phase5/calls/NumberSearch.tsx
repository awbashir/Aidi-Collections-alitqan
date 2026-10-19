/**
 * Search by the client's phone number (customer request 2026-10-19): the call list and the call
 * monitor. The server normalises the number (digits only, matched on the last 9 digits), so
 * 05xxxxxxxx, 9665xxxxxxxx and +9665xxxxxxxx find the same calls; 4 to 8 digits search part of
 * a number. The value is applied on Enter, on blur, or after a short pause in typing, and only
 * when it is empty or has at least 4 digits.
 */
import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { HiOutlineX } from 'react-icons/hi'
import { Search } from 'lucide-react'
import Input from '@/components/ui/Input'

const ARABIC = '٠١٢٣٤٥٦٧٨٩'
const PERSIAN = '۰۱۲۳۴۵۶۷۸۹'
export const MIN_NUMBER_DIGITS = 4

/** The ASCII digits of a typed number (Arabic-Indic digits included). */
export const numberDigits = (value: string) =>
    Array.from(value)
        .map((c) => {
            const a = ARABIC.indexOf(c)
            if (a >= 0) return String(a)
            const p = PERSIAN.indexOf(c)
            if (p >= 0) return String(p)
            return /[0-9]/.test(c) ? c : ''
        })
        .join('')

const NumberSearch = ({
    value,
    onChange,
    testId,
    width = 220,
}: {
    value: string | undefined
    onChange: (v: string | undefined) => void
    testId?: string
    width?: number
}) => {
    const { t } = useTranslation()
    const [text, setText] = useState(value || '')
    const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
    useEffect(() => setText(value || ''), [value])
    useEffect(() => () => {
        if (timer.current) clearTimeout(timer.current)
    }, [])
    const digits = numberDigits(text)
    const valid = !text.trim() || digits.length >= MIN_NUMBER_DIGITS
    const apply = (raw: string) => {
        if (timer.current) clearTimeout(timer.current)
        const next = raw.trim() ? raw.trim() : undefined
        if (next && numberDigits(next).length < MIN_NUMBER_DIGITS) return
        if ((next || undefined) !== (value || undefined)) onChange(next)
    }
    return (
        <div style={{ width }} data-testid={testId}>
            <div className="text-eyebrow mb-1">{t('p9.monfix.search.label')}</div>
            <Input
                size="sm"
                dir="ltr"
                inputMode="tel"
                autoComplete="off"
                value={text}
                placeholder={String(t('p9.monfix.search.placeholder'))}
                aria-label={String(t('p9.monfix.search.label'))}
                aria-invalid={!valid || undefined}
                title={String(valid ? t('p9.monfix.search.hint') : t('p9.monfix.search.tooShort'))}
                prefix={<Search aria-hidden size={14} className="text-gray-500" />}
                suffix={
                    text ? (
                        <button
                            type="button"
                            aria-label={String(t('p9.monfix.search.clear'))}
                            className="rounded p-0.5 text-gray-600 hover:bg-gray-100 dark:hover:bg-night-raised"
                            onClick={() => {
                                setText('')
                                apply('')
                            }}
                        >
                            <HiOutlineX />
                        </button>
                    ) : undefined
                }
                onChange={(e) => {
                    const v = e.target.value
                    setText(v)
                    if (timer.current) clearTimeout(timer.current)
                    timer.current = setTimeout(() => apply(v), 600)
                }}
                onKeyDown={(e) => {
                    if (e.key === 'Enter') apply(text)
                }}
                onBlur={() => apply(text)}
            />
            {!valid && <div className="mt-1 text-[11.5px] text-warning">{t('p9.monfix.search.tooShort')}</div>}
        </div>
    )
}

export default NumberSearch
