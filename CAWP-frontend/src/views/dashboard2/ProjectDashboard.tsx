import { useEffect, useRef } from 'react'
import type { ReactNode } from 'react'
import reducer, {
    getProjectDashboardData,
    getAllCollectorsKPI,
    getCommentsReportData,
    getAdminDailyPayments,
    getAdminDailyPaymentDates,
    useAppDispatch,
    useAppSelector,
} from './store'
import { injectReducer } from '@/store'
import Loading from '@/components/shared/Loading'
import ErrorBoundary from '@/components/shared/ErrorBoundary'
import ProjectDashboardHeader from './components/ProjectDashboardHeader'
import TaskOverview from './components/TaskOverview'
import KPICollector from './components/KPICollector'
import  CommentReportDaily from './components/CommentsReportDaily'
import CallsReportDaily from './components/CallsReportDaily'
import DailyPaymentsAll from './components/DailyPaymentsAll'
import AllCollectorsKPI from './components/AllCollectorsKPI'
import DailyPaymentsAllAdmin from './components/DailyPaymentsAllAdmin'
import DailyPromisePaymentsAllAdmin from './components/DailyPromisePaymentsAllAdmin'
import HomeDashboard from './home/HomeDashboard'
import { useInView } from './home/safe'


import { useTranslation } from 'react-i18next'

injectReducer('projectDashboard', reducer)

/** Runs `onSeen` once, the first time the block comes near the viewport (legacy cards below the fold). */
const WhenVisible = ({ onSeen, children, minHeight = 240 }: { onSeen: () => void; children: ReactNode; minHeight?: number }) => {
    const { ref, seen } = useInView<HTMLDivElement>()
    const fired = useRef(false)
    useEffect(() => {
        if (seen && !fired.current) {
            fired.current = true
            onSeen()
        }
    }, [seen, onSeen])
    return (
        <div ref={ref} className="flex flex-col gap-4" style={seen ? undefined : { minHeight }}>
            {seen ? children : null}
        </div>
    )
}

const ProjectDashboard = () => {
    const dispatch = useAppDispatch()

    const kPICollector = useAppSelector(
        (state) => state.projectDashboard?.data?.kPICollector
    )
    const  CommentsReportData = useAppSelector(
        (state) => state.projectDashboard?.data?.commentsReportData
    )
    const loadingKPICollector = useAppSelector(
        (state) => state.projectDashboard?.data?.loadingKPICollector
    )
    const loadingCommentsReportData = useAppSelector(
        (state) => state.projectDashboard?.data?.loadingCommentsReportData
    )

    const allCollectorsKPI = useAppSelector(
        (state) => state.projectDashboard?.data?.allCollectorsKPI
    )
    const loadingAllCollectorsKPI = useAppSelector(
        (state) => state.projectDashboard?.data?.loadingAllCollectorsKPI
    )

    const adminDailyPayments = useAppSelector(
        (state) => state.projectDashboard?.data?.adminDailyPayments
    )
    const loadingAdminDailyPayments = useAppSelector(
        (state) => state.projectDashboard?.data?.loadingAdminDailyPayments
    )
    const adminDailyPaymentDates = useAppSelector(
        (state) => state.projectDashboard?.data?.adminDailyPaymentDates
    )
    const loadingAdminDailyPaymentDates = useAppSelector(
        (state) => state.projectDashboard?.data?.loadingAdminDailyPaymentDates
    )

     const permissions = (useAppSelector((state) => state.auth?.user?.permissions) || {}) as Record<string, boolean | undefined>;
     const authName = useAppSelector((state) => state.auth?.user?.name);

    /* Home dashboard v2 (2026-10-20) sits above the legacy cards. The legacy cards now load
       when scrolled near (they are below the fold), each one inside its own error boundary so
       a broken card can never blank the page. */
    const fetchCollector = () => {
        if (permissions.dashbord_user) {
            dispatch(getProjectDashboardData())
        }
    }
    const fetchAdminPayments = () => {
        if (permissions.dashbord_admin) {
            dispatch(getAdminDailyPayments())
            dispatch(getAdminDailyPaymentDates())
        }
    }
    const fetchAdminCollectors = () => {
        if (permissions.dashbord_admin) {
            dispatch(getAllCollectorsKPI())
        }
    }
    const fetchAdminReports = () => {
        if (permissions.dashbord_admin) {
            dispatch(getCommentsReportData())
        }
    }
    const { t } = useTranslation()

    return (
        <div className="flex flex-col gap-4 h-full">

             {permissions.dashbord_user && (
                <ErrorBoundary name="header">
                    <ProjectDashboardHeader
                        userName={kPICollector?.userName || authName}
                        taskCount={kPICollector?.taskCount}
                    />
                </ErrorBoundary>
             )}

             {(permissions.dashbord_admin || permissions.dashbord_user) && (
                <ErrorBoundary name="home-v2">
                    <HomeDashboard admin={!!permissions.dashbord_admin} />
                </ErrorBoundary>
             )}

             {(permissions.dashbord_admin || permissions.dashbord_user) && (
                <h4 className="mt-2 text-[15px] font-extrabold text-navy-600 dark:text-night-strong">
                    {t('p10.homedash.legacyTitle')}
                </h4>
             )}

             {permissions.dashbord_user && (
                <WhenVisible onSeen={fetchCollector} minHeight={480}>
             <Loading loading={loadingKPICollector}>
                 <ErrorBoundary name="KPICollector" resetKeys={[kPICollector]}>
                 <KPICollector
                     className="lg:col-span-3 w-full h-full"
                     data={kPICollector}
                 />
                 </ErrorBoundary>
           <div className="grid grid-cols-1 lg:grid-cols-10 gap-4">
    <ErrorBoundary name="TaskOverview" resetKeys={[kPICollector]} className="lg:col-span-7">
    <TaskOverview
        data={kPICollector?.comments}
        className="lg:col-span-7"
    />
    </ErrorBoundary>
   <ErrorBoundary name="DailyPaymentsAll" resetKeys={[kPICollector]} className="lg:col-span-3">
   <DailyPaymentsAll
        className="lg:col-span-3 w-full h-full"
        data={kPICollector?.all_user_payments}
    />
    </ErrorBoundary>
</div>
               {/*  <Leads data={recentLeadsData} /> */}
               </Loading>
                </WhenVisible>
  )}

 {permissions.dashbord_admin && (
    <>
      <WhenVisible onSeen={fetchAdminPayments} minHeight={360}>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <>
        <Loading loading={loadingAdminDailyPayments}>
         <ErrorBoundary name="DailyPaymentsAllAdmin" resetKeys={[adminDailyPayments]}>
         <DailyPaymentsAllAdmin
            className="w-full h-full"
            data={adminDailyPayments?.daily_payments}
        />
        </ErrorBoundary>
        </Loading>
        <Loading loading={loadingAdminDailyPaymentDates}>
         <ErrorBoundary name="DailyPromisePaymentsAllAdmin" resetKeys={[adminDailyPaymentDates]}>
         <DailyPromisePaymentsAllAdmin
            className="w-full h-full"
            data={adminDailyPaymentDates?.payment_dates}
        />
        </ErrorBoundary>
        </Loading>
        </>
    </div>
      </WhenVisible>

    <WhenVisible onSeen={fetchAdminCollectors} minHeight={360}>
    <Loading loading={loadingAllCollectorsKPI}>
      <ErrorBoundary name="AllCollectorsKPI" resetKeys={[allCollectorsKPI]}>
      <AllCollectorsKPI data={allCollectorsKPI} />
      </ErrorBoundary>
    </Loading>
    </WhenVisible>

    <WhenVisible onSeen={fetchAdminReports} minHeight={360}>
    <Loading loading={loadingCommentsReportData}>
        <ErrorBoundary name="CommentsReportDaily" resetKeys={[CommentsReportData]}>
        <CommentReportDaily
            data={CommentsReportData}
        />
        </ErrorBoundary>
        <ErrorBoundary name="CallsReportDaily" resetKeys={[CommentsReportData]}>
        <CallsReportDaily
            data={CommentsReportData}
        />
        </ErrorBoundary>
    </Loading>
    </WhenVisible>


    </>
)}

{/*permissions.dashbord_admin && (
    <>
    <Loading loading={loadingAllCollectorsKPI}>
      <AllCollectorsKPI data={allCollectorsKPI} />
    </Loading>
    </>
)*/}


        </div>
    )
}

export default ProjectDashboard
