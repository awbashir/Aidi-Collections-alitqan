import {
    AXIS,
    AXIS_TICK,
    CHART_FONT_FAMILY,
    COLORS,
    GRID,
    LABEL,
    LEGEND,
} from '@/constants/chart.constant'
import type { ApexOptions } from 'apexcharts'

/**
 * Aidi AI Enterprise — ApexCharts chrome.
 *
 * Grid lines are horizontal only, the tooltip is navy, axis labels are 11.5px
 * tabular and data labels stay off. The tooltip *fill* cannot be set from
 * options, so it lives in `src/assets/styles/vendors/_apex-chart.css`
 * alongside the dark-theme variants; everything here is what Apex can own.
 *
 * NOTE: `Chart.tsx` deep-clones these through `JSON.parse(JSON.stringify(...))`,
 * which silently drops functions. Anything that has to survive to the canvas
 * must be plain data.
 */

/** 11.5px `#6B7482`, tabular. `apex-axis-label` carries the tabular numerals. */
const axisLabelStyle = {
    colors: LABEL,
    fontSize: '11.5px',
    fontFamily: CHART_FONT_FAMILY,
    fontWeight: 400,
    cssClass: 'apex-axis-label',
}

/** 1px `#E7EEF2`, horizontal only — no vertical grid on a time series. */
const gridDefault: ApexOptions['grid'] = {
    show: true,
    borderColor: GRID,
    strokeDashArray: 0,
    position: 'back',
    xaxis: {
        lines: {
            show: false,
        },
    },
    yaxis: {
        lines: {
            show: true,
        },
    },
    /* Apex's own defaults — anything tighter clips the y-axis labels. */
    padding: {
        top: 0,
        right: 10,
        bottom: 0,
        left: 12,
    },
}

/** Axis line 1px `#D7E1E8`; ticks `#8A929F` but hidden — the grid carries it. */
const xaxisDefault: ApexOptions['xaxis'] = {
    categories: [],
    axisBorder: {
        show: true,
        color: AXIS,
    },
    axisTicks: {
        show: false,
        color: AXIS_TICK,
    },
    labels: {
        style: axisLabelStyle,
    },
    tooltip: {
        enabled: false,
    },
}

const yaxisDefault: ApexOptions['yaxis'] = {
    labels: {
        style: axisLabelStyle,
    },
    axisBorder: {
        show: false,
        color: AXIS,
    },
    axisTicks: {
        show: false,
        color: AXIS_TICK,
    },
}

/**
 * 12px `#2E3450`, 8px swatch at radius 2, above the plot and left-aligned.
 * Apex writes `itemMargin.horizontal` to both sides of every item, so 8 is the
 * 16px gap the spec asks for.
 */
const legendDefault: ApexOptions['legend'] = {
    show: true,
    position: 'top',
    horizontalAlign: 'left',
    fontSize: '12px',
    fontFamily: CHART_FONT_FAMILY,
    fontWeight: 400,
    labels: {
        colors: LEGEND,
    },
    markers: {
        width: 8,
        height: 8,
        radius: 2,
        offsetY: 0,
    },
    itemMargin: {
        horizontal: 8,
        vertical: 6,
    },
}

/**
 * Navy fill / white 12.5px text / radius 6 all come from `_apex-chart.css`,
 * which hangs off `.apexcharts-theme-light` — so the theme stays `light` here
 * rather than fighting it.
 */
const tooltipDefault: ApexOptions['tooltip'] = {
    theme: 'light',
    fillSeriesColor: false,
    style: {
        fontSize: '12.5px',
        fontFamily: CHART_FONT_FAMILY,
    },
}

const chartDefault: ApexOptions['chart'] = {
    fontFamily: CHART_FONT_FAMILY,
    foreColor: LABEL,
    zoom: {
        enabled: false,
    },
    toolbar: {
        show: false,
    },
}

export const apexLineChartDefaultOption: ApexOptions = {
    chart: { ...chartDefault },
    colors: [...COLORS],
    dataLabels: {
        enabled: false,
    },
    /* 2px; an emphasised series steps up to 2.5 via `customOptions`. */
    stroke: {
        width: 2,
        curve: 'smooth',
        lineCap: 'round',
    },
    grid: { ...gridDefault },
    legend: { ...legendDefault },
    xaxis: { ...xaxisDefault },
    yaxis: { ...yaxisDefault },
    tooltip: { ...tooltipDefault },
}

export const apexAreaChartDefaultOption = { ...apexLineChartDefaultOption }

export const apexBarChartDefaultOption: ApexOptions = {
    chart: { ...chartDefault },
    plotOptions: {
        bar: {
            horizontal: false,
            /* 70% of the band = a 30% gap, per the dataviz spec. */
            columnWidth: '70%',
            borderRadius: 2,
            borderRadiusApplication: 'end',
        },
    },
    colors: [...COLORS],
    dataLabels: {
        enabled: false,
    },
    /* A transparent stroke is how Apex spaces grouped bars: 4px inner gap. */
    stroke: {
        show: true,
        width: 4,
        colors: ['transparent'],
    },
    grid: { ...gridDefault },
    legend: { ...legendDefault },
    xaxis: { ...xaxisDefault },
    yaxis: { ...yaxisDefault },
    fill: {
        opacity: 1,
    },
    tooltip: { ...tooltipDefault },
}

export const apexDonutChartDefaultOption: ApexOptions = {
    chart: {
        fontFamily: CHART_FONT_FAMILY,
        foreColor: LABEL,
    },
    colors: [...COLORS],
    plotOptions: {
        pie: {
            donut: {
                labels: {
                    show: true,
                    total: {
                        show: true,
                        showAlways: true,
                        label: '',
                        formatter: function (w) {
                            return w.globals.seriesTotals.reduce(
                                (a: string, b: string) => {
                                    return a + b
                                },
                                0
                            )
                        },
                    },
                },
                size: '85%',
            },
        },
    },
    stroke: {
        colors: ['transparent'],
    },
    labels: [],
    dataLabels: {
        enabled: false,
    },
    legend: {
        show: false,
    },
    tooltip: { ...tooltipDefault },
}

export const apexSparklineChartDefultOption: ApexOptions = {
    chart: {
        type: 'line',
        fontFamily: CHART_FONT_FAMILY,
        sparkline: {
            enabled: true,
        },
    },
    colors: [...COLORS],
    stroke: {
        width: 2,
        curve: 'smooth',
    },
    tooltip: {
        ...tooltipDefault,
        fixed: {
            enabled: false,
        },
        x: {
            show: false,
        },
        y: {
            title: {
                formatter: function () {
                    return ''
                },
            },
        },
        marker: {
            show: false,
        },
    },
}
