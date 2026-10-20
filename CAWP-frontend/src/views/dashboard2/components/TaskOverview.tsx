import { useState, useEffect } from 'react'
import classNames from 'classnames'
import Card from '@/components/ui/Card'
import Segment from '@/components/ui/Segment'
import Badge from '@/components/ui/Badge'
import Loading from '@/components/shared/Loading'
import Chart from '@/components/shared/Chart'
import { COLORS, COLORS_DARK } from '@/constants/chart.constant'
import { useConfig } from '@/components/ui/ConfigProvider'
import isEmpty from 'lodash/isEmpty'
import { useAppSelector } from '../store'
import { useTranslation } from "react-i18next";
import { format, parseISO } from "date-fns";


type ProjectOverviewChart = {
    onGoing: number
    finished: number
    total: number
    series: {
        name: string
        data: number[]
    }[]
    range: string[]
}

type TaskOverviewProps = {
    data?: {
        chart?: Record<string, ProjectOverviewChart>
    }
    className?: string
}

type ChartLegendProps = {
    label: string
    value: number
    badgeColor?: string
    showBadge?: boolean
}

const ChartLegend = ({
    label,
    value,
    badgeColor,
    showBadge = true,
}: ChartLegendProps) => {
    return (
        <div className="flex gap-2">
            {/* The swatch takes a literal colour, not a utility class: the
                `.badge-dot` orange fill (and its `.dark` variant) outranks any
                `bg-*` handed through `innerClass`, which is what silently
                turned these two swatches orange. */}
            {showBadge && <Badge className="mt-2.5" color={badgeColor} />}
            <div>
                <h5 className="font-extrabold tabular-nums text-navy-600 dark:text-night-strong">
                    {value}
                </h5>
                <p className="text-[12.5px] text-gray-600 dark:text-night-muted">
                    {label}
                </p>
            </div>
        </div>
    )
}

function transformCommentsData(comments, t, mode = "daily", statusFilter = "all") {
  let range = [];
  let activeSeries = [];
  let notActiveSeries = [];
  let filteredComments = [];

   // Ensure comments is an array before proceeding
  if (!Array.isArray(comments)) {
    return {
      [mode]: {
        active: 0,
        notActive: 0,
        total: 0,
        series: [
          { name: t("active_"), data: [] },
          { name: t("inactive_"), data: [] }
        ],
        range: []
      }
    };
  }

  // Apply status filter first (rows without a date are skipped: nothing may throw here)
  let statusFilteredComments = comments.filter(c => c && c.create_at);
  if (statusFilter === "active") {
    statusFilteredComments = comments?.filter(c => c?.status?.is_active);
  } else if (statusFilter === "inactive") {
    statusFilteredComments = comments?.filter(c => c?.status && !c.status.is_active);
  }
  // If statusFilter === "all", use all comments

  if (mode === "daily") {
    range = [
      "08:00am", "09:00am", "10:00am", "11:00am", "12:00pm",
      "01:00pm", "02:00pm", "03:00pm", "04:00pm", "05:00pm"
    ];
    activeSeries = new Array(range.length).fill(0);
    notActiveSeries = new Array(range.length).fill(0);

    const today = new Date().toISOString().split('T')[0];

    filteredComments = statusFilteredComments?.filter(comment => {
      const commentDate = new Date(comment.create_at);
      const commentDateStr = commentDate.toISOString().split('T')[0];
      return commentDateStr === today;
    });

    filteredComments.forEach(comment => {
      const commentDate = new Date(comment.create_at);
      const hour = commentDate.getHours();
      const index = hour - 8;
      if (index >= 0 && index < range.length) {
        if (comment?.status?.is_active) activeSeries[index]++;
        else notActiveSeries[index]++;
      }
    });

  } else if (mode === "weekly") {
    const days = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
    range = days;
    activeSeries = new Array(7).fill(0);
    notActiveSeries = new Array(7).fill(0);
    
    const now = new Date();
    const currentDay = now.getDay();
    const startOfWeek = new Date(now);
    startOfWeek.setDate(now.getDate() - currentDay);
    startOfWeek.setHours(0, 0, 0, 0);
    
    const endOfWeek = new Date(startOfWeek);
    endOfWeek.setDate(startOfWeek.getDate() + 6);
    endOfWeek.setHours(23, 59, 59, 999);
    
    filteredComments = statusFilteredComments?.filter(comment => {
      const commentDate = new Date(comment.create_at);
      return commentDate >= startOfWeek && commentDate <= endOfWeek;
    });

    filteredComments.forEach(comment => {
        const commentDate = new Date(comment.create_at);
        const day = commentDate.getDay();
        if (comment?.status?.is_active) activeSeries[day]++;
        else notActiveSeries[day]++;
    });

  } else if (mode === "monthly") {
    const grouped = {};
    
    const now = new Date();
    const startOfThreeMonthsAgo = new Date(now.getFullYear(), now.getMonth() - 2, 1);
    const endOfCurrentMonth = new Date(now.getFullYear(), now.getMonth() + 1, 0, 23, 59, 59, 999);
    
    filteredComments = statusFilteredComments?.filter(comment => {
      if (!comment.create_at) return false;
      const commentDate = new Date(comment.create_at);
      if (isNaN(commentDate.getTime())) return false;
      return commentDate >= startOfThreeMonthsAgo && commentDate <= endOfCurrentMonth;
    });

    filteredComments.forEach(comment => {
      try {
        const commentDate = new Date(comment.create_at);
        const monthYear = format(commentDate, "MMM yyyy");
        if (!grouped[monthYear]) grouped[monthYear] = { active: 0, notActive: 0 };
        if (comment?.status?.is_active) grouped[monthYear].active++;
        else grouped[monthYear].notActive++;
      } catch (error) {
        console.warn('Invalid date format for comment:', comment.create_at);
      }
    });

    range = Object.keys(grouped).sort((a, b) => {
      const dateA = new Date(a);
      const dateB = new Date(b);
      return dateA.getTime() - dateB.getTime();
    });
    
    activeSeries = range.map(month => grouped[month].active);
    notActiveSeries = range.map(month => grouped[month].notActive);
  }

  const activeComments = filteredComments.filter(c => c?.status?.is_active);
  const notActiveComments = filteredComments.filter(c => c?.status && !c.status.is_active);
  
  const activeCount = activeComments.length;
  const notActiveCount = notActiveComments.length;
  const totalCount = filteredComments.length;

  return {
    [mode]: {
      active: activeCount,
      notActive: notActiveCount,
      total: totalCount,
      series: [
        { name: t("active_"), data: activeSeries },
        { name: t("inactive_"), data: notActiveSeries }
      ],
      range
    }
  };
}

const TaskOverview = ({ data = {}, className }: TaskOverviewProps) => {
    const [timeRange, setTimeRange] = useState(['daily'])
    const [statusFilter, setStatusFilter] = useState(['all']) // Add status filter state
    

    const [repaint, setRepaint] = useState(false)
    const { t } = useTranslation();
    const { mode } = useConfig()

    /* This row IS the legend (the chart sets `legend: { show: false }`), so the
       swatches must be the same two hues Apex draws — including on dark, where
       `Chart` lifts them to their COLORS_DARK twins. */
    const seriesColors =
        mode === 'dark'
            ? [COLORS_DARK[0], COLORS_DARK[2]]
            : [COLORS[0], COLORS[2]]

    const sideNavCollapse = useAppSelector(
        (state) => state.theme.layout.sideNavCollapse
    )

    useEffect(() => {
        setRepaint(true)
        const timer1 = setTimeout(() => setRepaint(false), 300)

        return () => {
            clearTimeout(timer1)
        }
    }, [data, sideNavCollapse])

    const commentsData = transformCommentsData(data || [], t, timeRange[0] , statusFilter[0]);

    return (
        /* Elevation 0 — white, radius 8, 1px #D7E1E8 border, no shadow.
           `.card` / `.card-border` already carry the 20px padding. */
        <Card className={classNames('rounded-lg shadow-none', className)}>
            <div className="flex sm:flex-row flex-col md:items-center justify-between mb-5 gap-4">
                <h4 className="text-[15px] font-extrabold text-navy-600 dark:text-night-strong">
                    {t("comments.title")}
                </h4>
                 <div className="flex flex-col sm:flex-row gap-2">
                    {/* Time Range Segment */}
                    <Segment
                        value={timeRange}
                        size="sm"
                        onChange={(val: string | string[]) =>
                            setTimeRange(val as string[])
                        }
                    >
                        <Segment.Item value="monthly">{t("monthly")}</Segment.Item>
                        <Segment.Item value="weekly">{t("weekly")}</Segment.Item>
                        <Segment.Item value="daily">{t("daily")}</Segment.Item>
                    </Segment>
                    
                    {/* Status Filter Segment */}
                    <Segment
                        value={statusFilter}
                        size="sm"
                        onChange={(val: string | string[]) =>
                            setStatusFilter(val as string[])
                        }
                    >
                        <Segment.Item value="all">{t("all")}</Segment.Item>
                        <Segment.Item value="active">{t("active_")}</Segment.Item>
                        <Segment.Item value="inactive">{t("inactive_")}</Segment.Item>
                    </Segment>
                </div>
            </div>
            {!isEmpty(data) && !repaint && commentsData && (
                <>
                    <div className="flex items-center justify-between mb-4">
                        <div>
                            <ChartLegend
                                showBadge={false}
                                label={t("comments.total")}
                                value={commentsData[timeRange[0]].total}
                            />
                        </div>
                        <div className="flex gap-x-6">
                            {/* Swatches track the series colours below:
                                COLORS[0] navy and COLORS[2] steel — lifted to
                                their dark twins with the series. */}
                            <ChartLegend
                                badgeColor={seriesColors[0]}
                                label={commentsData[timeRange[0]].series[0].name}
                                value={commentsData[timeRange[0]].series[0].data.reduce((a, b) => a + b, 0)}
                            />
                            <ChartLegend
                                badgeColor={seriesColors[1]}
                                label={commentsData[timeRange[0]].series[1].name}
                                value={commentsData[timeRange[0]].series[1].data.reduce((a, b) => a + b, 0)}
                            />
                        </div>
                    </div>
                    <div>
                        <Chart
                            series={commentsData[timeRange[0]].series}
                            xAxis={commentsData[timeRange[0]].range}
                            type="bar"
                            customOptions={{
                                colors: [COLORS[0], COLORS[2]],
                                legend: { show: false },
                            }}
                        />
                    </div>
                </>
            )}
            <Loading loading={repaint} type="cover">
                {repaint && <div className="h-[300px]" />}
            </Loading>
        </Card>
    )
}

export default TaskOverview
