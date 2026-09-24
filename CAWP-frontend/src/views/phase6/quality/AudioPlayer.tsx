/**
 * The private QC recording player. The audio is fetched with byte-range requests
 * (`GET …/audio` with `Range`, 206 answers; services/v2/p6Api.loadQualityAudio) because the bearer
 * token cannot ride on an `<audio src>`, then played from memory, so it is fully seekable. Every
 * range read is audited by the API. A purged audio (410) or a missing file (404) is said as such.
 * The review editor reads the current position from here to capture a human-reviewed timestamp.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import type { MutableRefObject } from 'react'
import { useTranslation } from 'react-i18next'
import { Headphones } from 'lucide-react'
import Button from '@/components/ui/Button'
import { Ltr } from '@/components/v2/Display'
import { describeP6Error, loadQualityAudio } from '@/services/v2'
import type { AudioLoadProgress, QcAudio } from '@/services/v2'
import { Notice } from '@/views/phase5/parts'
import { formatBytes, formatStamp } from '../labels'

/** Files up to this size load by themselves when the recording opens. */
const AUTO_LOAD_BYTES = 6 * 1024 * 1024

const AudioPlayer = ({
    recordingId,
    audio,
    audioRef,
}: {
    recordingId: string
    audio: QcAudio
    audioRef: MutableRefObject<HTMLAudioElement | null>
}) => {
    const { t } = useTranslation()
    const [url, setUrl] = useState<string | null>(null)
    const [progress, setProgress] = useState<AudioLoadProgress | null>(null)
    const [loading, setLoading] = useState(false)
    const [error, setError] = useState<unknown>(null)
    const [time, setTime] = useState(0)
    const ctrl = useRef<AbortController | null>(null)
    const urlRef = useRef<string | null>(null)

    const load = useCallback(async () => {
        ctrl.current?.abort()
        const c = new AbortController()
        ctrl.current = c
        setLoading(true)
        setError(null)
        setProgress(null)
        try {
            const res = await loadQualityAudio(recordingId, audio.content_type, (p) => setProgress(p), c.signal)
            if (c.signal.aborted) return
            const u = URL.createObjectURL(res.blob)
            if (urlRef.current) URL.revokeObjectURL(urlRef.current)
            urlRef.current = u
            setUrl(u)
            setProgress(res.progress)
        } catch (e) {
            if ((e as { code?: string })?.code !== 'canceled') setError(e)
        } finally {
            if (!c.signal.aborted) setLoading(false)
        }
    }, [recordingId, audio.content_type])

    useEffect(() => {
        if (audio.available && audio.size_bytes <= AUTO_LOAD_BYTES) load()
        return () => {
            ctrl.current?.abort()
            if (urlRef.current) URL.revokeObjectURL(urlRef.current)
            urlRef.current = null
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [recordingId])

    if (!audio.available) {
        return (
            <Notice tone="neutral" testId="p6-qc-audio-purged">
                {t('p6.qc.audio.purged')}
            </Notice>
        )
    }
    const pct = progress ? Math.round((100 * progress.loaded) / Math.max(1, progress.total)) : 0
    return (
        <div data-testid="p6-qc-player" data-loaded={url ? '1' : '0'} data-requests={progress?.requests ?? ''} data-partial={progress?.partial ?? ''}>
            {url ? (
                <>
                    {/* eslint-disable-next-line jsx-a11y/media-has-caption */}
                    <audio
                        ref={audioRef}
                        src={url}
                        controls
                        preload="auto"
                        className="w-full"
                        data-testid="p6-qc-audio"
                        onTimeUpdate={(e) => setTime((e.target as HTMLAudioElement).currentTime)}
                        onSeeked={(e) => setTime((e.target as HTMLAudioElement).currentTime)}
                    />
                    <div className="mt-1 flex flex-wrap items-center justify-between gap-2 text-[12px] text-gray-600">
                        <span>
                            {t('p6.qc.audio.position')}:{' '}
                            <span dir="ltr" className="tabular-nums">
                                <span data-testid="p6-qc-audio-time">{formatStamp(time)}</span> / {formatStamp(audio.duration_seconds)}
                            </span>
                        </span>
                        <span data-testid="p6-qc-audio-ranges">
                            {t('p6.qc.audio.ranges', { requests: progress?.requests ?? 0, partial: progress?.partial ?? 0, size: formatBytes(audio.size_bytes) })}
                        </span>
                    </div>
                </>
            ) : (
                <div className="flex flex-wrap items-center gap-3">
                    <Button size="sm" icon={<Headphones size={15} />} loading={loading} onClick={load} data-testid="p6-qc-audio-load">
                        {t('p6.qc.audio.load', { size: formatBytes(audio.size_bytes) })}
                    </Button>
                    {loading && progress && (
                        <div className="flex min-w-[200px] flex-1 items-center gap-2" aria-live="polite">
                            <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-gray-200 dark:bg-night-raised">
                                <div className="h-full bg-azure-600" style={{ width: `${pct}%` }} />
                            </div>
                            <Ltr className="text-[12px] text-gray-600">{pct} %</Ltr>
                        </div>
                    )}
                </div>
            )}
            {error ? (
                <Notice tone="danger" className="mt-2" testId="p6-qc-audio-error">
                    {describeP6Error(error, t)}
                </Notice>
            ) : null}
            <div className="mt-2 text-[11.5px] text-gray-500">{t('p6.qc.audio.note')}</div>
        </div>
    )
}

export default AudioPlayer
