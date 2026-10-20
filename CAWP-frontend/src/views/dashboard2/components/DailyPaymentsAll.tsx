import classNames from 'classnames'
import Card from '@/components/ui/Card'
import Avatar from '@/components/ui/Avatar'
import {
    HiOutlineSwitchHorizontal,
    HiOutlineShoppingCart,
} from 'react-icons/hi'
import { CiMedal } from "react-icons/ci";
import { GiTrophyCup } from "react-icons/gi";
import { TbCircleNumber4,TbCircleNumber5,TbCircleNumber6 } from "react-icons/tb";



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
                    className="bg-navy-50 text-navy-600 dark:bg-night-raised dark:text-azure-300"
                    icon={<GiTrophyCup />}
                />
            )
        case 1: // 2nd place - Silver
            return (
                <Avatar
                    className="bg-gray-100 text-gray-600 dark:bg-night-raised dark:text-night-muted"
                    icon={<CiMedal />}
                />
            )
        case 2: // 3rd place - Bronze
            return (
                <Avatar
                    className="bg-gray-100 text-gray-600 dark:bg-night-raised dark:text-night-muted"
                    icon={<CiMedal />}
                />
            )
        case 3: // 4th place
            return (
                <Avatar
                    className="bg-azure-50 text-azure-600 dark:bg-night-raised dark:text-azure-300"
                    icon={<TbCircleNumber4 />}
                />
            )
        case 4: // 5th place
            return (
                <Avatar
                    className="bg-azure-50 text-azure-600 dark:bg-night-raised dark:text-azure-300"
                    icon={<TbCircleNumber5 />}
                />
            )
        case 5: // 6th place
            return (
                <Avatar
                    className="bg-azure-50 text-azure-600 dark:bg-night-raised dark:text-azure-300"
                    icon={<TbCircleNumber6 />}
                />
            )
    }
}

const DailyPaymentsAll = (props: RecentAcivityProps) => {

    const { t } = useTranslation()
    const { className, title = t('dailyPayments'), extra } = props
    const data = Array.isArray(props.data) ? props.data.filter(Boolean) : []

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
                {extra}
            </div>
            <div className="mt-5">
                {data.map((timeline, index) => (
                    <div key={timeline.id ?? index}>
                            <div
                                key={index}
                                className="flex h-row items-center justify-between gap-3 border-b border-gray-200 dark:border-night-border last:border-b-0"
                            >
                                <div className="flex items-center gap-2">
                                    <ActionAvatar
                                        actionType={index}
                                    />
                                    <h6 className="text-[13.5px] font-extrabold text-navy-600 dark:text-night-strong">
                                        {timeline.user_name} 
                                    </h6>
                                </div>
                                <div className="text-end tabular-nums">
                                
                                  

                                          {/*  <h5 className="flex items-end gap-1">
                                                                        <span>
                                                                            <NumericFormat
                                                                                thousandSeparator
                                                                                displayType="text"
                                                                                value={timeline.amount}
                                                                                decimalScale={2}
                                                                                
                                                                            />
                                                                        </span>
                                                                           <SaudiRiyal size={16} className="inline" />
                                                                    </h5> */}
                                  
                                </div>
                            </div>
                      
                    </div>
                ))}
            </div>
        </Card>
    )
}

export default DailyPaymentsAll
