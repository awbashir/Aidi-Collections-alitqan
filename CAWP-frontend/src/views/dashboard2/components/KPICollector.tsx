import React from 'react'
import { useTranslation } from 'react-i18next'
import Card from '@/components/ui/Card'
import Avatar from '@/components/ui/Avatar'
import { HiOutlineCurrencyDollar, HiOutlineCash } from 'react-icons/hi'
import { MdOutlineDateRange } from "react-icons/md";
import { TbCalendarTime } from "react-icons/tb";
import { MdThumbDownAlt } from "react-icons/md";
import { SlTarget } from "react-icons/sl";
import { LuBadgePercent } from "react-icons/lu";
import { BiSolidUserAccount } from "react-icons/bi";






import { NumericFormat } from 'react-number-format'
import { SaudiRiyal } from 'lucide-react';
import Progress from '@/components/ui/Progress'


type KPICollectorProps = {
    data?: any[]
    className?: string
}
//calculate work days in month excluding fridays and saturdays and holidays
function  calculateWorkDays(year: number, month: number): number {
    let workDays = 0;
    const holidays = [ 
      
    ];
    
    const daysInMonth = new Date(year, month + 1, 0).getDate();
    for (let day = 1; day <= daysInMonth; day++) {
        const date = new Date(year, month, day);
        const isFriday = date.getDay() === 5;
        const isSaturday = date.getDay() === 6;
        const isHoliday = holidays.some(holiday => 
            holiday.getDate() === date.getDate() &&
            holiday.getMonth() === date.getMonth() &&
            holiday.getFullYear() === date.getFullYear()
        );
        if (!isFriday && !isSaturday && !isHoliday) {
            workDays++;
        }
    }
    return workDays;
}
// New function to calculate work days passed up to a specific date
function calculateWorkDaysPassed(year: number, month: number, day: number): number {
    let workDays = 0;
    const holidays = [ 
      
    ];
    
    for (let currentDay = 1; currentDay <= day; currentDay++) {
        const date = new Date(year, month, currentDay);
        const isFriday = date.getDay() === 5;
        const isSaturday = date.getDay() === 6;
        const isHoliday = holidays.some(holiday => 
            holiday.getDate() === date.getDate() &&
            holiday.getMonth() === date.getMonth() &&
            holiday.getFullYear() === date.getFullYear()
        );
        if (!isFriday && !isSaturday && !isHoliday) {
            workDays++;
        }
    }
    return workDays;
}
const KPICollector = ({ data = [], className }: KPICollectorProps) => {
    const { t } = useTranslation()
    
    // Get clients count
    const AccountCount = data?.clients?.length || 0;
    
    // Calculate total amount from clients
    const totalAmount = data?.clients?.reduce((acc, item) => acc + item.amount, 0) || 0
    
    // Calculate Monthly Target based on priority:
    // 1. User's personal target (if set)
    // 2. Contract target (if user has no personal target but has contract)
    // 3. Default to 0 if nothing is set
    const calculateMonthlyTarget = () => {
        const user = data?.user;
        const contract = data?.contract;
        
        // Check if user has personal targets set
        const hasUserTarget = user?.target_amount > 0 || user?.target_percent > 0;
        
        if (hasUserTarget) {
            // Use user's personal target
            if (user.target_is_percentage && user.target_percent > 0) {
                return Math.round((totalAmount * user.target_percent) / 100);
            } else if (!user.target_is_percentage && user.target_amount > 0) {
                return user.target_amount;
            }
        }
        
        // If no user target, check contract target
        if (user?.contract_id && contract) {
            if (contract.monthly_target_percent > 0) {
                return Math.round((totalAmount * contract.monthly_target_percent) / 100);
            } else if (contract.monthly_target_amount > 0) {
                return contract.monthly_target_amount;
            }
        }
        
        // Default fallback
        return 0;
    };
    
    const MonthlyTarget = calculateMonthlyTarget();
    
    // Work days calculations
    const WorkDays = calculateWorkDays(new Date().getFullYear(), new Date().getMonth());
    const daysPassed = calculateWorkDaysPassed(new Date().getFullYear(), new Date().getMonth(), new Date().getDate());

    // Calculate achieved amount for current month
    const achieved = data?.payments?.filter(payment => {
        const paymentDate = new Date(payment.payment_date);
        const currentDate = new Date();
        return paymentDate.getMonth() === currentDate.getMonth() && 
               paymentDate.getFullYear() === currentDate.getFullYear();
    })?.reduce((acc, item) => acc + item.amount, 0) || 0;

    // Get current month and year
    const currentMonth = new Date().toLocaleDateString('en-US', { month: 'long' })
    const currentYear = new Date().getFullYear()
    return (
        <Card className={className}>
           <div className="border-b border-gray-300 dark:border-night-border pb-4 mb-6">
            <h4 className="text-[15px] font-extrabold text-navy-600 dark:text-night-strong mb-3">
                {t('KPI')} {t('forMonth')} {currentMonth} {currentYear}
            </h4>
            <div className="flex flex-wrap gap-4 text-[12.5px] text-gray-600 dark:text-night-muted">
                <div className="flex items-center gap-1">
                    <span className="font-extrabold text-navy-600 dark:text-night-body">{t("count_work_days")}:</span>
                    <span className="tag tag-neutral !h-[22px] tabular-nums">
                        {WorkDays}
                    </span>
                </div>
                <div className="flex items-center gap-1">
                    <span className="font-extrabold text-navy-600 dark:text-night-body">{t("days_passed")}:</span>
                    <span className="tag tag-info !h-[22px] tabular-nums">
                        {daysPassed}
                    </span>
                </div>
                <div className="flex items-center gap-1">
                    <span className="font-extrabold text-navy-600 dark:text-night-body">{t("days_remaining")}:</span>
                    <span className="tag tag-neutral !h-[22px] tabular-nums">
                        {WorkDays - daysPassed}
                    </span>
                </div>
            </div>
        </div>

            <div className="mt-6 grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
             
              <div className="space-y-4">
                  <div className="flex items-center gap-2">
                        <div>
                            <Avatar
                                size={45}
                                className="bg-navy-50 text-navy-600 dark:bg-night-raised dark:text-azure-300"
                                icon={<BiSolidUserAccount className="text-2xl" />}
                            />
                        </div>
                        <div>
                            <p className="text-eyebrow">{t('accounts.count')}</p>
                            <h5 className="mt-1 flex items-end gap-1 text-[20px] font-extrabold text-navy-600 dark:text-night-strong tabular-nums">
                                <span>
                                    <NumericFormat
                                        thousandSeparator
                                        displayType="text"
                                        value={AccountCount}
                                    />
                                </span>
                                <span className="pb-0.5 text-[12.5px] font-normal text-gray-600 dark:text-night-muted">{t('accounts.singular')}</span>
                            </h5>
                        </div>
                    </div>

                    <div className="flex items-center gap-2">
                        <div>
                            <Avatar
                                size={45}
                                className="bg-navy-50 text-navy-600 dark:bg-night-raised dark:text-azure-300"
                                icon={<HiOutlineCash className="text-2xl" />}
                            />
                        </div>
                        <div>
                            <p className="text-eyebrow">{t('totalAmount')}</p>
                            <h5 className="mt-1 flex items-end gap-1 text-[20px] font-extrabold text-navy-600 dark:text-night-strong tabular-nums">
                                <span>
                                     <NumericFormat
                                        thousandSeparator
                                        displayType="text"
                                        value={totalAmount}
                                        decimalScale={2}
                                    />
                                </span>
                                <span className="pb-0.5 text-[12.5px] font-normal text-gray-600 dark:text-night-muted">
                                    <SaudiRiyal size={16} className="inline" />
                                    </span>
                            </h5>
                        </div>
                    </div>

                    <div className="flex items-center gap-2">
                        <div>
                            <Avatar
                                size={45}
                                className="bg-navy-50 text-navy-600 dark:bg-night-raised dark:text-azure-300"
                                icon={<SlTarget className="text-2xl" />}
                            />
                        </div>
                        <div>
                            <p className="text-eyebrow">{t('monthlyTarget')}</p>
                            <h5 className="mt-1 flex items-end gap-1 text-[20px] font-extrabold text-navy-600 dark:text-night-strong tabular-nums">
                                <span>
                                     <NumericFormat
                                        thousandSeparator
                                        displayType="text"
                                        value={MonthlyTarget}
                                        decimalScale={2}
                                    />
                                </span>
                                <span className="pb-0.5 text-[12.5px] font-normal text-gray-600 dark:text-night-muted">
                                    <SaudiRiyal size={16} className="inline" />
                                </span>
                            </h5>
                        </div>
                    </div>
                </div>

                {/* Second Column */}
                <div className="space-y-4">
                    <div className="flex items-center gap-2">
                        <div>
                            <Avatar
                                size={45}
                                className="bg-azure-50 text-azure-600 dark:bg-night-raised dark:text-azure-300"
                                icon={<HiOutlineCurrencyDollar className="text-2xl" />}
                            />
                        </div>
                        <div>
                            <p className="text-eyebrow">{t('dailyTargetMinimumFromStartOfMonth')}</p>
                            <h5 className="mt-1 flex items-end gap-1 text-[20px] font-extrabold text-navy-600 dark:text-night-strong tabular-nums">
                                <span>
                                     <NumericFormat
                                        decimalScale={2}
                                        thousandSeparator
                                        displayType="text"
                                        value={MonthlyTarget / WorkDays}
                                    />
                                </span>
                                <span className="pb-0.5 text-[12.5px] font-normal text-gray-600 dark:text-night-muted">
                                    <SaudiRiyal size={16} className="inline" />
                                </span>
                            </h5>
                        </div>
                    </div>

                    <div className="flex items-center gap-2">
                        <div>
                            <Avatar
                                size={45}
                                className="bg-success-tint text-success dark:bg-night-raised dark:text-success"
                                icon={<HiOutlineCash className="text-2xl" />}
                            />
                        </div>
                        <div>
                            <p className="text-eyebrow">{t('achieved')}</p>
                            <h5 className="mt-1 flex items-end gap-1 text-[20px] font-extrabold text-navy-600 dark:text-night-strong tabular-nums">
                                <span>
                                     <NumericFormat
                                        thousandSeparator
                                        displayType="text"
                                        value={achieved}
                                         decimalScale={2}
                                        fixedDecimalScale={true}
                                    />
                                </span>
                                <span className="pb-0.5 text-[12.5px] font-normal text-gray-600 dark:text-night-muted">
                                    <SaudiRiyal size={16} className="inline" />
                                </span>
                            </h5>
                        </div>
                    </div>

                    <div className="flex items-center gap-2">
                        <div>
                            <Avatar
                                size={45}
                                className="bg-gray-100 text-gray-600 dark:bg-night-raised dark:text-night-muted"
                                icon={<HiOutlineCurrencyDollar className="text-2xl" />}
                            />
                        </div>
                        <div>
                            <p className="text-eyebrow">{t('remainingFromMonthlyTarget')}</p>
                            <h5 className="mt-1 flex items-end gap-1 text-[20px] font-extrabold text-navy-600 dark:text-night-strong tabular-nums">
                                <span>
                                     <NumericFormat
                                        thousandSeparator
                                        displayType="text"
                                        value={Math.round((MonthlyTarget - achieved))}
                                    />
                                </span>
                                <span className="pb-0.5 text-[12.5px] font-normal text-gray-600 dark:text-night-muted">
                                    <SaudiRiyal size={16} className="inline" />
                                </span>
                            </h5>
                        </div>
                    </div>
                </div>

                {/* Third Column */}
                <div className="space-y-4">
                    <div className="flex items-center gap-2">
                        <div>
                            <Avatar
                                size={45}
                                className="bg-info-tint text-info dark:bg-night-raised dark:text-azure-300"
                                icon={<SlTarget className="text-2xl" />}
                            />
                        </div>
                        <div>
                            <p className="text-eyebrow">{t('cumulativeDailyTarget')}</p>
                            <h5 className="mt-1 flex items-end gap-1 text-[20px] font-extrabold text-navy-600 dark:text-night-strong tabular-nums">
                                <span>
                                     <NumericFormat
                                        thousandSeparator
                                        displayType="text"
                                        value={Math.round(((MonthlyTarget - achieved) / (WorkDays - daysPassed)))}
                                    />
                                </span>
                                <span className="pb-0.5 text-[12.5px] font-normal text-gray-600 dark:text-night-muted">
                                    <SaudiRiyal size={16} className="inline" />
                                </span>
                            </h5>
                        </div>
                    </div>

                    <div className="flex items-center gap-2">
                        <div>
                            <Avatar
                                size={45}
                                className="bg-teal-100 text-teal-600 dark:bg-teal-500/20 dark:text-teal-100"
                                icon={<HiOutlineCash className="text-2xl" />}
                            />
                        </div>
                        <div>
                            <p className="text-eyebrow">{t('expectedToDate')}</p>
                            <h5 className="mt-1 flex items-end gap-1 text-[20px] font-extrabold text-navy-600 dark:text-night-strong tabular-nums">
                                <span>
                                     <NumericFormat
                                        thousandSeparator
                                        displayType="text"
                                        value={Math.round(((MonthlyTarget / WorkDays) * daysPassed))}
                                    />
                                </span>
                                <span className="pb-0.5 text-[12.5px] font-normal text-gray-600 dark:text-night-muted">
                                    <SaudiRiyal size={16} className="inline" />
                                </span>
                            </h5>
                        </div>
                    </div>

                    <div className="flex items-center gap-2">
                        <div>
                            <Avatar
                                size={45}
                                className="bg-indigo-100 text-indigo-600 dark:bg-indigo-500/20 dark:text-indigo-100"
                                icon={<LuBadgePercent className="text-2xl" />}
                            />
                        </div>
                        <div>
                            <p className="text-eyebrow">{t('achieved_percent')}</p>
                            <h5 className="mt-1 flex items-end gap-1 text-[20px] font-extrabold text-navy-600 dark:text-night-strong tabular-nums">

                                <span >
                                    <NumericFormat
                                        thousandSeparator
                                        fixedDecimalScale={2}
                                         decimalScale={2}
                                        displayType="text"
                                        value={achieved/MonthlyTarget * 100 || 0}
                                    />
                                    %
                                </span>
                            </h5>
                        </div>
                    </div>
                </div>
                { /* Fourth Column  */}
               <div className="space-y-4">
                    <div className="flex items-center gap-2">
                        <div>
                            <Avatar
                                size={45}
                                className="bg-info-tint text-info dark:bg-night-raised dark:text-azure-300"
                                icon={<MdThumbDownAlt className="text-2xl" />}
                            />
                        </div>
                        <div>
                            <p className="text-eyebrow">{t('deficit')}</p>
                           <h5 className="flex items-end gap-1">
    <span>
         <NumericFormat
            thousandSeparator
            displayType="text"
            className={Math.round((Math.round(((MonthlyTarget / WorkDays) * daysPassed)-achieved))) < 0 ? 'text-green-600' : 'text-red-600'}
            value={Math.round((Math.round(((MonthlyTarget / WorkDays) * daysPassed)-achieved)))}
        />
    </span>
    <span className="text-sm">
        <SaudiRiyal size={16} className={`inline ${Math.round((Math.round(((MonthlyTarget / WorkDays) * daysPassed)-achieved))) < 0 ? 'text-green-600' : 'text-red-600'}`} />
    </span>
</h5>
                        </div>
                    </div>

                    <div className="flex items-center gap-2">
                        <div>
                            <Avatar
                                size={45}
                                className="bg-teal-100 text-teal-600 dark:bg-teal-500/20 dark:text-teal-100"
                                icon={<MdOutlineDateRange className="text-2xl" />}
                            />
                        </div>
                        <div>
                            <p className="text-eyebrow">{t('dailyAppointments')}</p>
                            <h5 className="mt-1 flex items-end gap-1 text-[20px] font-extrabold text-navy-600 dark:text-night-strong tabular-nums">
                                <span>
                                     <NumericFormat
                                        decimalScale={2}
                                        fixedDecimalScale={2}
                                        thousandSeparator
                                        displayType="text"
                                        value={0}
                                    />
                                </span>
                                <span className="pb-0.5 text-[12.5px] font-normal text-gray-600 dark:text-night-muted">
                                    <SaudiRiyal size={16} className="inline" />
                                </span>
                            </h5>
                        </div>
                    </div>

                    <div className="flex items-center gap-2">
                        <div>
                            <Avatar
                                size={45}
                                className="bg-indigo-100 text-indigo-600 dark:bg-indigo-500/20 dark:text-indigo-100"
                                icon={<TbCalendarTime className="text-2xl" />}
                            />
                        </div>
                        <div>
                            <p className="text-eyebrow">{t('deficitFromTarget')}</p>
                            <h5 className="mt-1 flex items-end gap-1 text-[20px] font-extrabold text-navy-600 dark:text-night-strong tabular-nums">

                                <span >
                                    <NumericFormat
                                        thousandSeparator
                                        fixedDecimalScale={2}
                                         decimalScale={2}
                                        displayType="text"
                                        value={0}
                                    />
                                   
                                </span>
                                  <span className="pb-0.5 text-[12.5px] font-normal text-gray-600 dark:text-night-muted">
                                    <SaudiRiyal size={16} className="inline" />
                                </span>
                            </h5>
                        </div>
                    </div>
                </div>

            </div>
        </Card>
    )
}

export default KPICollector