import appConfig from '@/configs/app.config'
import { REDIRECT_URL_KEY } from '@/constants/app.constant'
import { Navigate, Outlet, useLocation } from 'react-router-dom'
import useAuth from '@/utils/hooks/useAuth'

const { unAuthenticatedEntryPath } = appConfig

const ProtectedRoute = () => {
    const { authenticated } = useAuth()

    const location = useLocation()

    if (!authenticated) {
        /* Keep the query and the hash (list state) through sign-in, so a
         * shared list link still opens where it pointed. Encoded, because a
         * raw `#` would end the sign-in URL's own query. */
        const target = `${location.pathname}${location.search}${location.hash}`
        return (
            <Navigate
                replace
                to={`${unAuthenticatedEntryPath}?${REDIRECT_URL_KEY}=${encodeURIComponent(target)}`}
            />
        )
    }

    return <Outlet />
}

export default ProtectedRoute
