import classNames from 'classnames'
import Card from '@/components/ui/Card'
import Avatar from '@/components/ui/Avatar'
import Button from '@/components/ui/Button'
import {
    HiOutlineSwitchHorizontal,
    HiOutlineShoppingCart,
    HiOutlineDownload,
} from 'react-icons/hi'
import { CiMedal } from "react-icons/ci";
import { GiTrophyCup } from "react-icons/gi";
import { TbCircleNumber1,TbCircleNumber2,TbCircleNumber3,TbCircleNumber4,TbCircleNumber5,TbCircleNumber6 } from "react-icons/tb";

import StatTile from './StatTile'
import type { Acivity } from '../store'
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { NumericFormat } from 'react-number-format'
import { SaudiRiyal } from 'lucide-react';

export type RecentAcivityProps = {
    data?: Acivity[]
    className?: string
    title?: string
    extra?: ReactNode
}

const ActionAvatar = ({ actionType }: { actionType: number }) => {
    switch (actionType) {
        case 0: // 1st place - Gold
            return (
                <Avatar
                    className="bg-blue-100 text-blue-600 dark:bg-blue-500/20 dark:text-blue-400"
                    icon={<TbCircleNumber1 />}
                />
            )
        case 1: // 2nd place - Silver
            return (
                <Avatar
                    className="bg-blue-100 text-blue-600 dark:bg-blue-500/20 dark:text-blue-400"
                    icon={<TbCircleNumber2 />}
                />
            )
        case 2: // 3rd place - Bronze
            return (
                <Avatar
                    className="bg-blue-100 text-blue-600 dark:bg-blue-500/20 dark:text-blue-400"
                    icon={<TbCircleNumber3 />}
                />
            )
        case 3: // 4th place
            return (
                <Avatar
                    className="bg-blue-100 text-blue-600 dark:bg-blue-500/20 dark:text-blue-400"
                    icon={<TbCircleNumber4 />}
                />
            )
        case 4: // 5th place
            return (
                <Avatar
                    className="bg-blue-100 text-blue-600 dark:bg-blue-500/20 dark:text-blue-400"
                    icon={<TbCircleNumber5 />}
                />
            )
        case 5: // 6th place
            return (
                <Avatar
                    className="bg-blue-100 text-blue-600 dark:bg-blue-500/20 dark:text-blue-400"
                    icon={<TbCircleNumber6 />}
                />
            )
    }
}

const DailyPromisePaymentsAllAdmin = (props: RecentAcivityProps) => {

    const { t } = useTranslation()
    const { className, title = t('dailyAppointments'), extra } = props
    const data = Array.isArray(props.data) ? props.data.filter(Boolean) : []

    // Calculate statistics
    const totalPayment = data.reduce((sum, timeline) => sum + (timeline.amount || 0), 0)
    const count = data.length
    const averagePayment = count > 0 ? totalPayment / count : 0
    const highestPayment = data.length > 0 ? Math.max(...data.map(timeline => timeline.amount || 0)) : 0


    const handleExport = () => {
    // Create CSV content
    const headers = ['User Name', 'Contract Name', 'Amount']
    const csvContent = [
        headers.join(','),
        ...data.map(timeline => [
            `"${(timeline.user_name || '').replace(/"/g, '""')}"`,
            `"${(timeline.contract_name || '').replace(/"/g, '""')}"`,
            timeline.amount || 0
        ].join(',')),
        `"Total",,${totalPayment}`
    ].join('\n')

    // Add UTF-8 BOM for proper encoding
    const BOM = '\uFEFF'
    const csvWithBOM = BOM + csvContent

    // Create and download file
    const blob = new Blob([csvWithBOM], { type: 'text/csv;charset=utf-8;' })
    const link = document.createElement('a')
    const url = URL.createObjectURL(blob)
    link.setAttribute('href', url)
    link.setAttribute('download', `daily-payments-${new Date().toISOString().split('T')[0]}.csv`)
    link.style.visibility = 'hidden'
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    URL.revokeObjectURL(url)
}
    return (
        <Card className={className}>
            <div className="flex items-start justify-between gap-4">
                <div>
                    <h4 className="text-[15px] font-extrabold text-navy-600 dark:text-night-strong">
                        {title}
                    </h4>
                    <p className="mt-1 text-[12.5px] text-gray-600 dark:text-night-muted tabular-nums">
                        {new Date().toLocaleDateString()}
                    </p>
                </div>
                <div className="flex items-center gap-2">
                    {extra}
                    <Button
                        size="sm"
                        variant="default"
                        icon={<HiOutlineDownload />}
                        onClick={handleExport}
                    >
                        {t('export')}
                    </Button>
                </div>
            </div>

            {/* KPI row — one lead measure carries the orange rail. */}
            <div className="mt-5 grid grid-cols-2 gap-4">
                <StatTile
                    label={t('total')}
                    unit={<SaudiRiyal size={13} className="inline" />}
                    value={
                        <NumericFormat
                            thousandSeparator
                            displayType="text"
                            value={totalPayment}
                            decimalScale={2}
                        />
                    }
                />
                <StatTile label={t('count')} value={count} />
                <StatTile
                    label={t('average')}
                    unit={<SaudiRiyal size={13} className="inline" />}
                    value={
                        <NumericFormat
                            thousandSeparator
                            displayType="text"
                            value={averagePayment}
                            decimalScale={2}
                        />
                    }
                />
                <StatTile
                    label={t('highest')}
                    unit={<SaudiRiyal size={13} className="inline" />}
                    value={
                        <NumericFormat
                            thousandSeparator
                            displayType="text"
                            value={highestPayment}
                            decimalScale={2}
                        />
                    }
                />
            </div>

            <div className="mt-section">
                <div className="max-h-96 overflow-y-auto">
                    {data.map((timeline, index) => (
                        <div
                            key={timeline.id ?? index}
                            className="flex h-row items-center gap-4 border-b border-gray-200 dark:border-night-border last:border-b-0"
                        >
                            <span className="flex-1 truncate text-[13.5px] font-extrabold text-navy-600 dark:text-night-strong">
                                {timeline.user_name}
                            </span>
                            <span className="flex-1 truncate text-[13.5px] text-gray-600 dark:text-night-muted">
                                {timeline.contract_name}
                            </span>
                            <span className="flex shrink-0 items-end justify-end gap-1 text-[13.5px] font-extrabold text-navy-600 dark:text-night-strong tabular-nums">
                                <NumericFormat
                                    thousandSeparator
                                    displayType="text"
                                    value={timeline.amount}
                                    decimalScale={2}
                                />
                                <SaudiRiyal size={13} className="inline" />
                            </span>
                        </div>
                    ))}
                    {data.length === 0 && (
                        <div className="py-8 text-center text-[13.5px] text-gray-600 dark:text-night-muted">
                            {t('noData')}
                        </div>
                    )}
                </div>
            </div>
        </Card>
    )
}

export default DailyPromisePaymentsAllAdmin
