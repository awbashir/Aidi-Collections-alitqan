import { useEffect } from 'react'
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
import ProjectDashboardHeader from './components/ProjectDashboardHeader'
import TaskOverview from './components/TaskOverview'
import KPICollector from './components/KPICollector'
import  CommentReportDaily from './components/CommentsReportDaily'
import CallsReportDaily from './components/CallsReportDaily'
import DailyPaymentsAll from './components/DailyPaymentsAll'
import AllCollectorsKPI from './components/AllCollectorsKPI'
import DailyPaymentsAllAdmin from './components/DailyPaymentsAllAdmin'
import DailyPromisePaymentsAllAdmin from './components/DailyPromisePaymentsAllAdmin'


import { useTranslation } from 'react-i18next'

injectReducer('projectDashboard', reducer)

const ProjectDashboard = () => {
    const dispatch = useAppDispatch()

    const kPICollector = useAppSelector(
        (state) => state.projectDashboard.data.kPICollector
    )
    const  CommentsReportData = useAppSelector(
        (state) => state.projectDashboard.data.commentsReportData
    )
    const loadingKPICollector = useAppSelector(
        (state) => state.projectDashboard.data.loadingKPICollector
    )
    const loadingCommentsReportData = useAppSelector(
        (state) => state.projectDashboard.data.loadingCommentsReportData
    )

    const allCollectorsKPI = useAppSelector(
        (state) => state.projectDashboard.data.allCollectorsKPI
    )
    const loadingAllCollectorsKPI = useAppSelector(
        (state) => state.projectDashboard.data.loadingAllCollectorsKPI
    )

    const adminDailyPayments = useAppSelector(
        (state) => state.projectDashboard.data.adminDailyPayments
    )
    const loadingAdminDailyPayments = useAppSelector(
        (state) => state.projectDashboard.data.loadingAdminDailyPayments
    )
    const adminDailyPaymentDates = useAppSelector(
        (state) => state.projectDashboard.data.adminDailyPaymentDates
    )
    const loadingAdminDailyPaymentDates = useAppSelector(
        (state) => state.projectDashboard.data.loadingAdminDailyPaymentDates
    )

     const { permissions } = useAppSelector((state) => state.auth.user);

    useEffect(() => {
        fetchData()
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [])

    const fetchData = () => {
        if( permissions.dashbord_user){
            dispatch(getProjectDashboardData())
        }

        if( permissions.dashbord_admin){
            dispatch(getCommentsReportData())
            dispatch(getAllCollectorsKPI())
            dispatch(getAdminDailyPayments())
            dispatch(getAdminDailyPaymentDates())
        }
    }
    const { t } = useTranslation()

    return (
        <div className="flex flex-col gap-4 h-full">
          
             {permissions.dashbord_user && (
                <>
             <Loading loading={loadingKPICollector}>
               <ProjectDashboardHeader
                    userName={kPICollector?.userName}
                    taskCount={kPICollector?.taskCount}
                />

                
                 <KPICollector
                     className="lg:col-span-3 w-full h-full"
                     data={kPICollector}
                 />
           <div className="grid grid-cols-1 lg:grid-cols-10 gap-4">
    <TaskOverview
        data={kPICollector?.comments}
        className="lg:col-span-7"
    />
   <DailyPaymentsAll
        className="lg:col-span-3 w-full h-full"
        data={kPICollector?.all_user_payments}
    />    
</div>
               {/*  <Leads data={recentLeadsData} /> */}
               </Loading>
               </>
  )}
        
 {permissions.dashbord_admin && (
    <>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <>
        <Loading loading={loadingAdminDailyPayments}>
         <DailyPaymentsAllAdmin
            className="w-full h-full"
            data={adminDailyPayments?.daily_payments}
        />   
        </Loading> 
        <Loading loading={loadingAdminDailyPaymentDates}>
         <DailyPromisePaymentsAllAdmin
            className="w-full h-full"
            data={adminDailyPaymentDates?.payment_dates}
        />
        </Loading>
        </>
    </div>

    <Loading loading={loadingAllCollectorsKPI}>
      <AllCollectorsKPI data={allCollectorsKPI} />
    </Loading> 

    <Loading loading={loadingCommentsReportData}>
        <CommentReportDaily
            data={CommentsReportData}
        />
        <CallsReportDaily
            data={CommentsReportData}
        />
    </Loading>

    
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
