import { createSlice, createAsyncThunk } from '@reduxjs/toolkit'
import { apiGetProjectDashboardData ,apiGetCommentsReportData , apiGetAllCollectorsKPI,apiGetAdminDailyPayments,apiGetAdminDailyPaymentDates} from '@/services/DashService'

type ProjectOverviewChart = {
    onGoing: number
    finished: number
    total: number
    series: {
        name: string
        data: number[]
    }[]
    range: string[]
}

type DashboardData = {
    userName?: string
    taskCount?: number
    projectOverviewData?: {
        chart: {
            daily: ProjectOverviewChart
            weekly: ProjectOverviewChart
            monthly: ProjectOverviewChart
        }
    }
    myTasksData?: {
        taskId: string
        taskSubject: string
        priority: number
        assignees: {
            id: string
            name: string
            email: string
            img: string
        }[]
    }[]
    scheduleData?: {
        id: string
        time: string
        eventName: string
        desciption: string
        type: string
    }[]
    activitiesData?: {
        type: string
        dateTime: number
        ticket: string
        status?: number
        userName: string
        userImg?: string
        comment?: string
        tags?: string[]
        files?: string[]
    }[]
    projectsData?: {
        id: number
        name: string
        category: string
        desc: string
        attachmentCount: number
        totalTask: number
        completedTask: number
        progression: number
        dayleft: number
        status: string
        member: {
            name: string
            img: string
        }[]
    }[]
    selectedContractId?: string
}

type GetProjectDashboardDataResponse = DashboardData

export type ProjectDashboardState = {
    loading: boolean
    dashboardData: DashboardData
}

export const SLICE_NAME = 'projectDashboard'

export const getProjectDashboardData = createAsyncThunk(
    SLICE_NAME + '/getProjectDashboardData',
    async () => {
        const response =
            await apiGetProjectDashboardData<GetProjectDashboardDataResponse>()
        return response.data
    }
)

export const getCommentsReportData = createAsyncThunk(
    SLICE_NAME + '/getCommentsReportData',
    async () => {
        const response =
            await apiGetCommentsReportData<GetCommentsReportDataResponse>()
        return response.data
    }
)

export const getAllCollectorsKPI = createAsyncThunk(
    SLICE_NAME + '/getAllCollectorsKPI',
    async () => {
        const response =
            await apiGetAllCollectorsKPI<GetAllCollectorsKPIResponse>()
        return response.data
    }
)
//apiGetAdminDailyPayments
export const getAdminDailyPayments = createAsyncThunk(
    SLICE_NAME + '/getAdminDailyPayments',
    async () => {
        const response =
            await apiGetAdminDailyPayments<GetAdminDailyPaymentsResponse>()
        return response.data
    }
)

//apiGetAdminDailyPaymentDates
export const getAdminDailyPaymentDates = createAsyncThunk(
    SLICE_NAME + '/getAdminDailyPaymentDates',
    async () => {
        const response =
            await apiGetAdminDailyPaymentDates<GetAdminDailyPaymentDatesResponse>()
        return response.data
    }
)

const initialState: ProjectDashboardState = {
    kPICollector: {},
    loadingKPICollector: true,
    commentsReportData: {},
    loadingCommentsReportData: true,
    allCollectorsKPI: {},
    loadingAllCollectorsKPI: true,
    selectedContractId: 'all',
    adminDailyPayments:{},
    loadingAdminDailyPayments:true,
    adminDailyPaymentDates:{},
    loadingAdminDailyPaymentDates:true,
}


const projectDashboardSlice = createSlice({
    name: `${SLICE_NAME}/state`,
    initialState,
    reducers: {
        setSelectedContractId: (state, action) => {
            state.selectedContractId = action.payload
        }
    },
    extraReducers: (builder) => {
        builder
            .addCase(getProjectDashboardData.fulfilled, (state, action) => {
                state.kPICollector = action.payload
                state.loadingKPICollector = false
            })
            .addCase(getProjectDashboardData.pending, (state) => {
                state.loadingKPICollector = true
            })
            .addCase(getProjectDashboardData.rejected, (state) => {
                state.loadingKPICollector = false
            })
            .addCase(getCommentsReportData.fulfilled, (state, action) => {
                state.commentsReportData = action.payload
                state.loadingCommentsReportData = false
               
            })
            .addCase(getCommentsReportData.pending, (state) => {
                state.loadingCommentsReportData = true
            })
            .addCase(getCommentsReportData.rejected, (state) => {
                state.loadingCommentsReportData = false
            })
            .addCase(getAllCollectorsKPI.fulfilled, (state, action) => {
                state.allCollectorsKPI = action.payload
                state.loadingAllCollectorsKPI = false
            })
            .addCase(getAllCollectorsKPI.pending, (state) => {
                state.loadingAllCollectorsKPI = true
            })
            .addCase(getAllCollectorsKPI.rejected, (state) => {
                state.loadingAllCollectorsKPI = false
            })
            .addCase(getAdminDailyPayments.fulfilled, (state, action) => {
                state.adminDailyPayments = action.payload
                state.loadingAdminDailyPayments = false
            })
            .addCase(getAdminDailyPayments.pending, (state) => {
                state.loadingAdminDailyPayments = true
            })
            .addCase(getAdminDailyPayments.rejected, (state) => {
                state.loadingAdminDailyPayments = false
            })
            .addCase(getAdminDailyPaymentDates.fulfilled, (state, action) => {
                state.adminDailyPaymentDates = action.payload
                state.loadingAdminDailyPaymentDates = false
            })
            .addCase(getAdminDailyPaymentDates.pending, (state) => {
                state.loadingAdminDailyPaymentDates = true
            })
            .addCase(getAdminDailyPaymentDates.rejected, (state) => {
                state.loadingAdminDailyPaymentDates = false
            })
    },
})

export const { setSelectedContractId } = projectDashboardSlice.actions

export default projectDashboardSlice.reducer
