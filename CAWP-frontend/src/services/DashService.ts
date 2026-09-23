import ApiService from "./ApiService";

export async function apiGetSalesDashboardData<
  T,
  U extends Record<string, unknown>
>(data: U) {
  return ApiService.fetchData<T>({
    url: "/dashboard",
    method: "get",
    params: {
      startDate: data.startDate,
      endDate: data.endDate,
    },
  });
}

//apiGetSalesDashboardDataForCollector
export async function apiGetSalesDashboardDataForCollector<
  T,
  U extends Record<string, unknown>
>(data: U) {
  return ApiService.fetchData<T>({
    url: "/dashbord/collector",
    method: "get",
    params: {
      startDate: data.startDate,
      endDate: data.endDate,
    },
  });
}

//get /payment_date/user
export async function apiGetPaymentDateUser<
  T,
  U extends Record<string, unknown>
>(data: U) {
  return ApiService.fetchData<T>({
    url: "/payment_date/user",
    method: "get",
    params: {
      startDate: data.startDate,
      endDate: data.endDate,
    },
  });
}

export async function apiGetProjectDashboardData<
  T,
  U extends Record<string, unknown>
>(data: U) {
  return ApiService.fetchData<T>({
    url: "/dashbord/collector/v2",
    method: "get",
  });
}

///dashbord/Allcollector/v2
export async function apiGetAllCollectorsKPI<
  T,
  U extends Record<string, unknown>
>(data: U) {
  return ApiService.fetchData<T>({
    url: "/dashbord/Allcollector/v2",
    method: "get",
   
  });
}

///dashbord/admin/dailyPayments/v2
export async function apiGetAdminDailyPayments<
  T,
  U extends Record<string, unknown>
>(data: U) {
  return ApiService.fetchData<T>({
    url: "/dashbord/admin/dailyPayments/v2",
    method: "get",
   
  });
}
///dashbord/admin/dailyPaymentDates/v2
export async function apiGetAdminDailyPaymentDates<
  T,
  U extends Record<string, unknown>
>(data: U) {
  return ApiService.fetchData<T>({
    url: "/dashbord/admin/dailyPaymentDates/v2",
    method: "get",
  });
}

///dashbord/commentsReport/v2
export async function apiGetCommentsReportData<
  T,
  U extends Record<string, unknown>
>(data: U) {
  return ApiService.fetchData<T>({
    url: "/dashbord/commentsReport/v2",
    method: "get",
  });
}



/* export async function apiGetSalesProducts<T, U extends Record<string, unknown>>(
    data: U
) {
    return ApiService.fetchData<T>({
        url: '/sales/products',
        method: 'post',
        data,
    })
}

export async function apiDeleteSalesProducts<
    T,
    U extends Record<string, unknown>
>(data: U) {
    return ApiService.fetchData<T>({
        url: '/sales/products/delete',
        method: 'delete',
        data,
    })
}

export async function apiGetSalesProduct<T, U extends Record<string, unknown>>(
    params: U
) {
    return ApiService.fetchData<T>({
        url: '/sales/product',
        method: 'get',
        params,
    })
}

export async function apiPutSalesProduct<T, U extends Record<string, unknown>>(
    data: U
) {
    return ApiService.fetchData<T>({
        url: '/sales/products/update',
        method: 'put',
        data,
    })
}

export async function apiCreateSalesProduct<
    T,
    U extends Record<string, unknown>
>(data: U) {
    return ApiService.fetchData<T>({
        url: '/sales/products/create',
        method: 'post',
        data,
    })
}

export async function apiGetSalesOrders<T, U extends Record<string, unknown>>(
    params: U
) {
    return ApiService.fetchData<T>({
        url: '/sales/orders',
        method: 'get',
        params,
    })
}

export async function apiDeleteSalesOrders<
    T,
    U extends Record<string, unknown>
>(data: U) {
    return ApiService.fetchData<T>({
        url: '/sales/orders/delete',
        method: 'delete',
        data,
    })
}

export async function apiGetSalesOrderDetails<
    T,
    U extends Record<string, unknown>
>(params: U) {
    return ApiService.fetchData<T>({
        url: '/sales/orders-details',
        method: 'get',
        params,
    })
} */
