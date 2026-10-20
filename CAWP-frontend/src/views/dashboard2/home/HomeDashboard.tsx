/**
 * Home dashboard v2 (customer request 2026-10-20): interactive KPI cards and charts above the
 * legacy cards of `/home`.
 *
 *   admin (permission dashbord_admin): KPI cards, target gauge, collected by contract (click
 *     filters the page), collections by day vs target, promises by due day, notes today by
 *     status, notes heatmap, calls today, collectors leaderboard (click filters the page to one
 *     collector), recovery / ageing / amounts / reachability / neglect of the open cases;
 *   collector (dashbord_user only): the same cards on their own cases, with their target gauge,
 *     their promises by state, their notes and their calls.
 *
 * Page filters: period (today / last 7 days / month to date; KPI cards and leaderboard),
 * contract (admins) and collector (from the leaderboard). The API applies the scope. Every
 * section and card is its own error boundary and treats every field as optional; sections
 * below the fold mount (and load) only when scrolled near.
 */
import { useCallback, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { HiOutlineRefresh, HiX } from 'react-icons/hi'
import Button from '@/components/ui/Button'
import Select from '@/components/ui/Select'
import ErrorBoundary from '@/components/shared/ErrorBoundary'
import { Segmented } from '@/views/phase1/followUps/parts'
import { homeDashboardApi } from '@/services/v2/homeDashboardApi'
import type { HomePeriod, HomeQuery } from '@/services/v2/homeDashboardApi'
import type { Option } from '@/services/v2'
import { useV2Query } from '@/utils/v2/hooks'
import { useHomeLinks } from './context'
import type { HomeCtx } from './context'
import { LazySection } from './parts'
import { arr, text } from './safe'
import SummarySection from './SummarySection'
import ChartsSection from './ChartsSection'
import CallsCard from './CallsCard'
import Leaderboard from './Leaderboard'
import PortfolioSection from './PortfolioSection'

const PERIOD_KEY = 'cawp.home.period'
const PERIODS: HomePeriod[] = ['today', 'week', 'month']

const readPeriod = (): HomePeriod => {
    try {
        const v = window.localStorage.getItem(PERIOD_KEY)
        return PERIODS.includes(v as HomePeriod) ? (v as HomePeriod) : 'today'
    } catch {
        return 'today'
    }
}

const HomeDashboard = ({ admin }: { admin: boolean }) => {
    const { t } = useTranslation()
    const [period, setPeriodState] = useState<HomePeriod>(readPeriod)
    const [contract, setContract] = useState('')
    const [focus, setFocus] = useState<{ id: string; name: string } | null>(null)
    const [tick, setTick] = useState(0)

    const setPeriod = useCallback((p: HomePeriod) => {
        setPeriodState(p)
        try {
            window.localStorage.setItem(PERIOD_KEY, p)
        } catch {
            /* private mode: the choice is just not remembered */
        }
    }, [])

    const query: HomeQuery = useMemo(
        () => ({
            period,
            contract_id: admin && contract ? contract : undefined,
            user_id: admin && focus ? focus.id : undefined,
            nocache: tick ? '1' : undefined,
        }),
        [period, contract, focus, admin, tick]
    )
    const links = useHomeLinks(admin, admin ? contract : '', admin ? focus?.id : undefined)
    const ctx: HomeCtx = useMemo(
        () => ({ admin, period, contract, focus, query, setContract, setFocus, links }),
        [admin, period, contract, focus, query, links]
    )
    const summary = useV2Query((signal) => homeDashboardApi.summary(query, signal), [query.period, query.contract_id, query.user_id, tick])

    const contractOptions: Option[] = [
        { value: '', label: t('p10.homedash.allContracts') as string },
        ...arr(summary.data?.options?.contracts).map((c) => ({
            value: c.id,
            label: c?.is_active === false ? `${text(c?.name)} — ${t('p10.homedash.inactive')}` : text(c?.name),
        })),
    ]
    const selectedContract = contractOptions.find((o) => o.value === contract) || {
        value: contract,
        label: contract ? (t('p10.homedash.contract') as string) : (t('p10.homedash.allContracts') as string),
    }

    return (
        <div className="flex flex-col gap-4" data-testid="homedash">
            <div className="flex flex-wrap items-end justify-between gap-3" data-testid="homedash-filters">
                <div className="flex flex-wrap items-end gap-3">
                    <div className="flex flex-col gap-1 text-[12.5px]">
                        <span className="text-gray-600">{t('p10.homedash.period')}</span>
                        <Segmented<HomePeriod>
                            value={period}
                            onChange={setPeriod}
                            label={t('p10.homedash.period') as string}
                            testId="homedash-period"
                            options={PERIODS.map((p) => ({ value: p, label: t(`p10.homedash.periods.${p}`) }))}
                        />
                    </div>
                    {admin && (
                        <label className="flex w-full max-w-[260px] flex-col gap-1 text-[12.5px]">
                            <span className="text-gray-600">{t('p10.homedash.contract')}</span>
                            <Select
                                size="sm"
                                isSearchable
                                options={contractOptions}
                                value={selectedContract}
                                onChange={(o) => setContract((o as Option | null)?.value || '')}
                            />
                        </label>
                    )}
                    {admin && focus && (
                        <span className="mb-1 inline-flex items-center gap-1 rounded-full border border-azure-600 bg-azure-50 px-3 py-1 text-[12.5px] font-extrabold text-navy-600 dark:bg-night-raised dark:text-gray-100">
                            {t('p10.homedash.collectorFilter', { name: focus.name })}
                            <button type="button" aria-label={t('p10.homedash.clearFilter') as string} onClick={() => setFocus(null)} className="ms-1 text-gray-600 hover:text-danger">
                                <HiX />
                            </button>
                        </span>
                    )}
                </div>
                <Button size="sm" icon={<HiOutlineRefresh />} loading={summary.loading && !!summary.data} onClick={() => setTick((n) => n + 1)}>
                    {t('p10.homedash.refresh')}
                </Button>
            </div>

            <ErrorBoundary name="summary" resetKeys={[summary.data]}>
                <SummarySection ctx={ctx} q={summary} />
            </ErrorBoundary>

            <LazySection minHeight={640}>
                <ErrorBoundary name="charts">
                    <ChartsSection key={`charts-${tick}`} ctx={ctx} />
                </ErrorBoundary>
            </LazySection>

            <LazySection minHeight={300}>
                <ErrorBoundary name="calls">
                    <CallsCard key={`calls-${tick}`} ctx={ctx} />
                </ErrorBoundary>
            </LazySection>

            {admin && (
                <LazySection minHeight={360}>
                    <ErrorBoundary name="leaderboard">
                        <Leaderboard key={`leaders-${tick}`} ctx={ctx} />
                    </ErrorBoundary>
                </LazySection>
            )}

            {admin && (
                <LazySection minHeight={640}>
                    <ErrorBoundary name="portfolio">
                        <PortfolioSection key={`portfolio-${tick}`} ctx={ctx} />
                    </ErrorBoundary>
                </LazySection>
            )}

            <p className="text-[12px] text-gray-600" data-testid="homedash-rules">
                {t('p10.homedash.rules')}
            </p>
        </div>
    )
}

export default HomeDashboard
