/**
 * The call monitor's charts (customer request 2026-10-20, replacing the per-user bar chart and
 * the over-time chart): every chart follows the page's period, user and number filters (the
 * `GET /v2/calling/monitor/stats` response it is given), with tooltips and legend toggles.
 *
 * - Calls over time (TimelineChart): by outcome / direction / user, zoomable; a day opens by hour.
 * - Calls by user (UsersChart): the call mix per user, sortable, top N; a bar opens the user's calls.
 * - Answer rate (AnswerRateChart): the team's gauges with their denominators + per user.
 * - Talk-time distribution (TalkTimeChart): answered calls by talk-time bucket.
 * - Average ring / talk time per user (AvgTimesChart).
 * - Busy hours (HeatmapChart): weekday x hour, for a period of several days.
 */
import type { MonitorStats, MonitorUserStats } from '@/services/v2'
import AnswerRateChart from './AnswerRateChart'
import AvgTimesChart from './AvgTimesChart'
import HeatmapChart from './HeatmapChart'
import TalkTimeChart from './TalkTimeChart'
import TimelineChart from './TimelineChart'
import UsersChart from './UsersChart'

const MonitorCharts = ({
    data,
    onOpenUser,
    onDrillDay,
}: {
    data: MonitorStats
    onOpenUser: (u: MonitorUserStats) => void
    onDrillDay: (day: string) => void
}) => (
    <div className="flex flex-col gap-4" data-testid="p10-monitor-charts">
        <TimelineChart data={data} onDrillDay={onDrillDay} />
        <div className="grid gap-4 xl:grid-cols-2">
            <UsersChart users={data.users} onOpenUser={onOpenUser} />
            <AnswerRateChart data={data} onOpenUser={onOpenUser} />
            <TalkTimeChart data={data} />
            <AvgTimesChart data={data} onOpenUser={onOpenUser} />
        </div>
        <HeatmapChart data={data} />
    </div>
)

export default MonitorCharts
