import reducer, { useAppSelector } from './store'
import { injectReducer } from '@/store'
import ErrorBoundary from '@/components/shared/ErrorBoundary'
import ProjectDashboardHeader from './components/ProjectDashboardHeader'
import HomeDashboard from './home/HomeDashboard'

injectReducer('projectDashboard', reducer)

/* Home page: the greeting and the home dashboard v2 (2026-10-20). 2026-10-29 (customer request):
   everything that sat under "مزيد من التفاصيل" (the legacy monthly KPI, payments, collectors and
   daily notes/calls cards) is removed; the card components stay in ./components. */
const ProjectDashboard = () => {
    const permissions = (useAppSelector((state) => state.auth?.user?.permissions) || {}) as Record<string, boolean | undefined>
    const authName = useAppSelector((state) => state.auth?.user?.name)

    return (
        <div className="flex flex-col gap-4 h-full">
            {permissions.dashbord_user && (
                <ErrorBoundary name="header">
                    <ProjectDashboardHeader userName={authName} />
                </ErrorBoundary>
            )}

            {(permissions.dashbord_admin || permissions.dashbord_user) && (
                <ErrorBoundary name="home-v2">
                    <HomeDashboard admin={!!permissions.dashbord_admin} />
                </ErrorBoundary>
            )}
        </div>
    )
}

export default ProjectDashboard
