import { useRef, useEffect, useCallback, useMemo } from 'react'
import ApexChart from 'react-apexcharts'
import {
    apexLineChartDefaultOption,
    apexBarChartDefaultOption,
    apexAreaChartDefaultOption,
    apexDonutChartDefaultOption,
} from '@/configs/chart.config'
import { DIR_RTL } from '@/constants/theme.constant'
import { useConfig } from '@/components/ui/ConfigProvider'
import { COLORS, COLORS_DARK } from '@/constants/chart.constant'
import type { ApexOptions } from 'apexcharts'
import type { Direction } from '@/@types/theme'
import type { ReactNode } from 'react'

const notDonut = ['line', 'bar', 'area']

type ChartType = 'line' | 'bar' | 'area' | 'donut'

export interface ChartProps {
    series?: ApexOptions['series']
    width?: string | number
    height?: string | number
    /* eslint-disable @typescript-eslint/no-explicit-any */
    xAxis?: any
    customOptions?: ApexOptions
    type?: ChartType
    direction?: Direction
    donutTitle?: string | ReactNode
    donutText?: string | ReactNode
    className?: string
}

const Chart = (props: ChartProps) => {
    const {
        series = [],
        width = '100%',
        height = 300,
        xAxis,
        customOptions,
        type = 'line',
        direction,
        donutTitle,
        donutText,
        className,
        ...rest
    } = props

    const chartRef = useRef<HTMLDivElement>(null)
    const { mode } = useConfig()
    const isDark = mode === 'dark'

    /* Light hue -> its dark-theme twin. Built from the two ramps so a chart
     * that hand-picks a subset (`colors: [COLORS[0], COLORS[2]]`) lifts too,
     * not just the default palette. */
    const darkOf = useMemo(
        () =>
            new Map(
                COLORS.map((color, index) => [
                    color.toUpperCase(),
                    COLORS_DARK[index],
                ])
            ),
        []
    )

    const chartDefaultOption = useMemo(() => {
        switch (type) {
            case 'line':
                return apexLineChartDefaultOption
            case 'bar':
                return apexBarChartDefaultOption
            case 'area':
                return apexAreaChartDefaultOption
            case 'donut':
                return apexDonutChartDefaultOption
            default:
                return apexLineChartDefaultOption
        }
    }, [type])

    let options = JSON.parse(JSON.stringify(chartDefaultOption))
    const isMobile = window.innerWidth < 768

    const setLegendOffset = useCallback(() => {
        const lengend = chartRef.current?.querySelector<HTMLDivElement>(
            'div.apexcharts-legend'
        )

        /* Apex renders asynchronously and a chart may have no legend at all
         * (donuts, sparklines, `legend: { show: false }`), so there is nothing
         * to offset until the node exists. */
        if (!lengend) {
            return
        }

        if (direction === DIR_RTL) {
            /* The legend mirrors with the layout even though the plot does
             * not: `direction: rtl` on the flex row flows the items, their
             * swatches and their text from the inline-start edge. */
            lengend.style.direction = 'rtl'
            lengend.style.right = 'auto'
            lengend.style.left = '0'
        }
        if (isMobile) {
            lengend.style.position = 'relative'
            lengend.style.top = '0'
            lengend.style.justifyContent = 'start'
            lengend.style.padding = '0'
        }
    }, [direction, isMobile])

    useEffect(() => {
        if (notDonut.includes(type as ChartType)) {
            setLegendOffset()
        }
    }, [type, setLegendOffset])

    if (notDonut.includes(type as ChartType)) {
        options.xaxis.categories = xAxis
    }

    if (customOptions) {
        options = { ...options, ...customOptions }
    }

    /* Dark theme takes the lifted series ramp — navy on #060820 is invisible.
     * Apex reads `colors` when it constructs, so the chart is keyed on the
     * mode below to force a rebuild when the theme is toggled. */
    if (isDark) {
        const current: string[] = options.colors?.length
            ? options.colors
            : COLORS
        options.colors = current.map(
            (color) => darkOf.get(String(color).toUpperCase()) ?? color
        )
    }

    if (type === 'donut') {
        if (donutTitle) {
            options.plotOptions.pie.donut.labels.total.label = donutTitle
        }
        if (donutText) {
            options.plotOptions.pie.donut.labels.total.formatter = () =>
                donutText
        }
    }

    return (
        /* The plot never mirrors under RTL: a time or category axis is
         * numeric, oldest stays left, and Apex lays the canvas out from the
         * container's writing direction. Forcing `ltr` here is what keeps the
         * x axis from flipping — the legend is mirrored back on its own in
         * `setLegendOffset`. */
        <div
            ref={chartRef}
            style={direction === DIR_RTL ? { direction: 'ltr' } : {}}
            className="chartRef"
        >
            <ApexChart
                key={mode}
                options={options}
                type={type}
                series={series}
                width={width}
                height={height}
                className={className}
                {...rest}
            />
        </div>
    )
}

export default Chart
