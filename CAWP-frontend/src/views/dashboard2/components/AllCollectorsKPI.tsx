import React, { useState, useMemo ,useEffect } from 'react'
import Select from '@/components/ui/Select'
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
import  {setSelectedContractId} from '../store/'

type CollectorData = {
    user: {
        id: number;
        name: string;
        target_amount?: number;
        target_percent?: number;
        target_is_percentage?: boolean;
        contract_id?: number;
    };
    clients: Array<{ amount: number }>;
    payments: Array<{ amount: number; payment_date: string }>;
    contract?: {
        monthly_target_amount?: number;
        monthly_target_percent?: number;
    };
}

type AllCollectorsKPIProps = {
    data?: CollectorData[]
    className?: string
}

type ApiResponse = {
    clients: Array<{
        id: number;
        amount: number;
        user_id: number;
    }>;
    payments: Array<{
        id: number;
        amount: number;
        payment_date: string;
        user_id: number;
    }>;
    users: Array<{
        id: number;
        name: string;
        target_amount?: number;
        target_percent?: number;
        target_is_percentage?: boolean;
        contract_id?: number;
    }>;
    contracts: Array<{
        id: number;
        name : string;
        monthly_target_amount?: number;
        monthly_target_percent?: number;
    }>;
};

// Transform function to convert API data to component format
// Every list may be missing and every lookup may miss (2026-10-20: a user whose contract was not in
// `contracts` crashed the whole home page), so nothing here assumes a field is present.
const transformApiDataToCollectors = (apiData: ApiResponse): CollectorData[] => {
    const list = <T,>(v: T[] | undefined | null): T[] => (Array.isArray(v) ? v : []);
    return list(apiData?.users).filter(Boolean).map(user => {
        // Find user's clients
        const userClients = list(apiData?.clients).filter(client => client?.user_id === user.id);
        
        // Find user's payments
        const userPayments = list(apiData?.payments).filter(payment => payment?.user_id === user.id);
        
        // Find user's contract
        const userContract = list(apiData?.contracts).find(contract => contract?.id === user.contract_id);
        
        return {
            user: {
                id: user.id,
                name: [user.frist_name, user.last_name].filter(Boolean).join(' ') || '—',
                target_amount: user.target_amount,
                target_percent: user.target_percent,
                target_is_percentage: user.target_is_percentage,
                contract_id: user.contract_id
            },
            clients: userClients.map(client => ({
                amount: Number(client?.amount) || 0
            })),
            payments: userPayments.map(payment => ({
                amount: Number(payment?.amount) || 0,
                payment_date: payment.payment_date
            })),
            contract: userContract ? {
                name: userContract.name,
                monthly_target_amount: userContract.monthly_target_amount,
                monthly_target_percent: userContract.monthly_target_percent
            } : undefined
        };
    });
};

//calculate work days in month excluding fridays and saturdays and holidays
function calculateWorkDays(year: number, month: number): number {
    let workDays = 0;
    const holidays = [];
    
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
    const holidays = [];
    
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

const AllCollectorsKPI = ({ data = [], className }: AllCollectorsKPIProps) => {
    const { t } = useTranslation()
    const [selectedContractId, setSelectedContractId] = useState<string>('all')


    useEffect(() => {
        setSelectedContractId(selectedContractId)
    }, [selectedContractId])

    
    // Transform API data to collectors data
    const collectorsData = transformApiDataToCollectors(data);

        // Get unique contracts for the select dropdown
    const contractOptions = useMemo(() => {
        const uniqueContracts = collectorsData
            .filter(collector => collector.contract)
            .reduce((acc, collector) => {
                const contract = collector.contract!;
                if (!acc.find(c => c.id === collector.user.contract_id)) {
                    acc.push({
                        id: collector.user.contract_id!,
                        name: contract.name
                    });
                }
                return acc;
            }, [] as { id: number; name: string }[]);
        
        return [
            { value: 'all', label: t('all_contracts') || 'All Contracts' },
            ...uniqueContracts.map(contract => ({
                value: String(contract.id),
                label: contract.name
            }))
        ];
    }, [collectorsData, t]);

    // Filter collectors based on selected contract
    const filteredCollectors = useMemo(() => {
        if (selectedContractId === 'all') {
            return collectorsData;
        }
        return collectorsData.filter(collector => 
            collector.user.contract_id?.toString() === selectedContractId
        );
    }, [collectorsData, selectedContractId]);
    
    // Calculate individual collector KPIs
    const calculateIndividualKPIs = (collector: CollectorData) => {
        const collectorTotalAmount = collector.clients?.reduce((acc, item) => acc + item.amount, 0) || 0;
        
        // Calculate monthly target for this collector
        const calculateCollectorTarget = () => {
            const user = collector.user;
            const contract = collector.contract;
            
            const hasUserTarget = user?.target_amount > 0 || user?.target_percent > 0;
            
            if (hasUserTarget) {
                if (user.target_is_percentage && user.target_percent > 0) {
                    return Math.round((collectorTotalAmount * user.target_percent) / 100);
                } else if (!user.target_is_percentage && user.target_amount > 0) {
                    return user.target_amount;
                }
            }
            
            if (user?.contract_id && contract) {
                if (contract.monthly_target_percent > 0) {
                    return Math.round((collectorTotalAmount * contract.monthly_target_percent) / 100);
                } else if (contract.monthly_target_amount > 0) {
                    return contract.monthly_target_amount;
                }
            }
            
            return 0;
        };
        
        const monthlyTarget = calculateCollectorTarget();
        
        // Calculate achieved amount for current month
        const achieved = collector.payments?.filter(payment => {
            const paymentDate = new Date(payment.payment_date);
            const currentDate = new Date();
            return paymentDate.getMonth() === currentDate.getMonth() && 
                   paymentDate.getFullYear() === currentDate.getFullYear();
        })?.reduce((acc, item) => acc + item.amount, 0) || 0;
        
        return {
            totalAccounts: collector.clients?.length || 0,
            totalAmount: collectorTotalAmount,
            monthlyTarget,
            achieved,
            remaining: monthlyTarget - achieved,
            achievementPercent: monthlyTarget > 0 ? (achieved / monthlyTarget) * 100 : 0
        };
    };
    
    // Calculate aggregated KPIs for all collectors
    const calculateAggregatedKPIs = () => {
        let totalAccounts = 0;
        let totalAmount = 0;
        let totalMonthlyTarget = 0;
        let totalAchieved = 0;

        filteredCollectors?.forEach(collector => {
        const individualKPIs = calculateIndividualKPIs(collector);
        totalAccounts += individualKPIs.totalAccounts;
        totalAmount += individualKPIs.totalAmount;
        totalMonthlyTarget += individualKPIs.monthlyTarget;
        totalAchieved += individualKPIs.achieved;
    });
        
        return {
            totalAccounts,
            totalAmount,
            totalMonthlyTarget,
            totalAchieved
        };
    };
    
    const { totalAccounts, totalAmount, totalMonthlyTarget, totalAchieved } = calculateAggregatedKPIs();
    
    // Work days calculations
    const WorkDays = calculateWorkDays(new Date().getFullYear(), new Date().getMonth());
    const daysPassed = calculateWorkDaysPassed(new Date().getFullYear(), new Date().getMonth(), new Date().getDate());

    // Get current month and year
    const currentMonth = new Date().toLocaleDateString('en-US', { month: 'long' })
    const currentYear = new Date().getFullYear()
    
    return (
        <Card className={className}>
            {/* Header Section */}
            <div className="border-b border-gray-300 dark:border-night-border pb-4 mb-6">
                <h4 className="text-[15px] font-extrabold text-navy-600 dark:text-night-strong mb-3">
                    {t('All Collectors')} {t('KPI')} {t('forMonth')} {currentMonth} {currentYear}
                </h4>

                  {/* Contract Filter */}
                    <div className="flex items-center gap-2">
                        <label className="text-[12.5px] font-extrabold text-navy-600 dark:text-night-body">
                            {t('filter_by_contract')}:
                        </label>
                        <Select
                            value={contractOptions.find(option => option.value === selectedContractId)}
                            options={contractOptions}
                            onChange={(option) => setSelectedContractId(option?.value || 'all')}
                            placeholder={t('select_contract')}
                            className="min-w-[200px]"
                            size='sm'
                            
                        />
                    </div>

                <div className="mt-4 flex flex-wrap gap-4 text-[12.5px] text-gray-600 dark:text-night-muted">
                    <div className="flex items-center gap-1">
                        <span className="font-extrabold text-navy-600 dark:text-night-body">{t("total_collectors")}:</span>
                        <span className="tag tag-neutral !h-[22px] tabular-nums">
                            {filteredCollectors.length}
                        </span>
                    </div>
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



            {/* Individual Collectors Section */}
            <div className="mt-section border-t border-gray-300 dark:border-night-border pt-6">
                 <h5 className="text-[15px] font-extrabold text-navy-600 dark:text-night-strong mb-4">
                    المؤشرات الرئيسية لشهر {currentMonth} {currentYear} لكل محصل
                    {selectedContractId !== 'all' && (
                        <span className="text-[12.5px] font-normal text-gray-600 dark:text-night-muted ms-2">
                            - {contractOptions.find(c => c.value === selectedContractId)?.label}
                        </span>
                    )}
                </h5>

                  {filteredCollectors.length === 0 ? (
                    <div className="text-center py-8">
                        <p className="text-[13.5px] text-gray-600 dark:text-night-muted">
                            {t('no_collectors_found')}
                        </p>
                    </div>
                ) : (
                
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                    {filteredCollectors.map((collector) => {
                        const kpis = calculateIndividualKPIs(collector);
                        
                        return (
                            <Card key={collector.user.id} className="border border-gray-300 dark:border-night-border">
                                <div className="flex items-center gap-3 mb-4">
                                    <Avatar
                                        size={40}
                                        className="bg-blue-100 text-blue-600 dark:bg-blue-500/20 dark:text-blue-100"
                                    >
                                        {(collector.user?.name || '?').charAt(0)}
                                    </Avatar>
                                    <div>
                                        <h6 className="text-[13.5px] font-extrabold text-navy-600 dark:text-night-strong">
                                            {collector.user?.name || '—'}
                                        </h6>
                                        <p className="text-[12.5px] text-gray-600 dark:text-night-muted">
                                            {collector.contract?.name ?? '—'}
                                        </p>
                                    </div>
                                </div>

                                <div className="space-y-3">
                                    {/* Accounts */}
                                    <div className="flex justify-between items-center">
                                        <span className="text-[12.5px] text-gray-600 dark:text-night-muted">
                                            {t('accounts.count')}:
                                        </span>
                                        <span className="text-[13.5px] font-extrabold text-navy-600 dark:text-night-strong tabular-nums">
                                            <NumericFormat
                                                thousandSeparator
                                                displayType="text"
                                                value={kpis.totalAccounts}
                                                
                                                suffix=' حساب '
                                                size={12}
                                            />
                                        </span>
                                    </div>

                                    {/* Total Amount */}
                                    <div className="flex justify-between items-center">
                                        <span className="text-[12.5px] text-gray-600 dark:text-night-muted">
                                            {t('total_amount')}:
                                        </span>
                                        <span className="text-[13.5px] font-extrabold text-navy-600 dark:text-night-strong tabular-nums">
                                            <NumericFormat
                                                thousandSeparator
                                                displayType="text"
                                                value={kpis.totalAmount}
                                                decimalScale={0}
                                            /> <SaudiRiyal size={12} className="inline ms-1" />
                                        </span>
                                    </div>

                                    {/* Monthly Target */}
                                    <div className="flex justify-between items-center">
                                        <span className="text-[12.5px] text-gray-600 dark:text-night-muted">
                                            {t('monthlyTarget')}:
                                        </span>
                                        <span className="text-[13.5px] font-extrabold text-navy-600 dark:text-night-strong tabular-nums">
                                            <NumericFormat
                                                thousandSeparator
                                                displayType="text"
                                                value={kpis.monthlyTarget}
                                                decimalScale={0}
                                            /> <SaudiRiyal size={12} className="inline ms-1" />
                                        </span>
                                    </div>

                                    {/* Achieved */}
                                    <div className="flex justify-between items-center">
                                        <span className="text-[12.5px] text-gray-600 dark:text-night-muted">
                                            {t('achieved')}:
                                        </span>
                                        <span className="text-[13.5px] font-extrabold text-success tabular-nums">
                                            <NumericFormat
                                                thousandSeparator
                                                displayType="text"
                                                value={kpis.achieved}
                                                decimalScale={0}
                                            /> <SaudiRiyal size={12} className="inline ms-1" />
                                        </span>
                                    </div>

                                    {/* Remaining */}
                                    <div className="flex justify-between items-center">
                                        <span className="text-[12.5px] text-gray-600 dark:text-night-muted">
                                            {t('remaining')}:
                                        </span>
                                        <span className={`text-[13.5px] font-extrabold tabular-nums ${kpis.remaining > 0 ? 'text-danger' : 'text-success'}`}>
                                            <NumericFormat
                                                thousandSeparator
                                                displayType="text"
                                                value={Math.abs(kpis.remaining)}
                                                decimalScale={0}
                                            /> <SaudiRiyal size={12} className="inline ms-1" />
                                        </span>
                                    </div>

                                    {/* Achievement Percentage */}
                                    <div className="mt-3">
                                        <div className="flex justify-between items-center mb-1">
                                            <span className="text-[12.5px] text-gray-600 dark:text-night-muted">
                                                {t('achieved_percent')}:
                                            </span>
                                            <span className="text-sm font-semibold text-gray-800 dark:text-gray-200">
                                                <NumericFormat
                                                    displayType="text"
                                                    value={kpis.achievementPercent}
                                                    decimalScale={1}
                                                    fixedDecimalScale={true}
                                                />%
                                            </span>
                                        </div>
                                        <Progress 
                                            percent={kpis.achievementPercent} 
                                            className="h-2"
                                            showInfo={false}
                                        />
                                    </div>
                                </div>
                            </Card>
                        );
                    })}
                </div>
                )}
            </div>

        </Card>
    )
}

export default AllCollectorsKPI