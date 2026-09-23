import ApiService from "./ApiService";

export async function apiGetCrmDashboardData<T>() {
  return ApiService.fetchData<T>({
    url: "user/dashboard",
    method: "get",
  });
}

export async function apiGetCrmCalendar<T>() {
  return ApiService.fetchData<T>({
    url: "user/calendar",
    method: "get",
  });
}

export async function apiGetCrmUsers<T, U extends Record<string, unknown>>(
  data: U
) {
  return ApiService.fetchData<T>({
    url: "/uesrs",
    method: "get",
    data,
    params: {
      ...data,
      field: data?.sort?.key,
      sort: data?.sort?.order,
      "customeFilterStatusesValue[]":
        data?.filterData?.customeFilterStatusesValue,
      "customeFilterArchivingStatusesValue[]":
        data?.filterData?.customeFilterArchivingStatusesValue,
      "customeFilterBranchesValue[]":
        data?.filterData?.customeFilterBranchesValue,
    },
  });
}

//export users

export async function apiGetUsersExport<T, U extends Record<string, unknown>>(
  data: U
) {
  return ApiService.fetchData<T>({
    url: "/uesrs/export",
    timeout: 0, // bulk Excel upload/export: may legitimately exceed the default timeout
    method: "get",
    data,
    params: {
      ...data,
      field: data?.sort?.key,
      sort: data?.sort?.order,
      "customeFilterStatusesValue[]":
        data.filterData.customeFilterStatusesValue,
      "customeFilterArchivingStatusesValue[]":
        data.filterData.customeFilterArchivingStatusesValue,
      "customeFilterBranchesValue[]":
        data.filterData.customeFilterBranchesValue,
    },
  });
}

//get nationalities
export async function apiGetNationalities<T>() {
  return ApiService.fetchData<T>({
    url: "/nationalities",
    method: "get",
  });
}

//get roles
export async function apiGetRoles<T>() {
  return ApiService.fetchData<T>({
    url: "/roles",
    method: "get",
  });
}

//get contracts
export async function apiGetContracts<T>() {
  return ApiService.fetchData<T>({
    url: "/contracts",
    method: "get",
  });
}

export async function apiGetBranches<T, U extends Record<string, unknown>>(
  data: U
) {
  return ApiService.fetchData<T>({
    url: "/branches",
    method: "get",
    data,
  });
}

export async function apiGetCrmUsersStatistic<T>() {
  return ApiService.fetchData<T>({
    url: "user/users-statistic",
    method: "get",
  });
}

export async function apPutCrmUser<T, U extends Record<string, unknown>>(
  data: U
) {
  return ApiService.fetchData<T>({
    url: "/uesrs/" + data.id,
    method: "put",
    data,
  });
}

export async function apiPostCrmUser<T, U extends Record<string, unknown>>(
  data: U
) {
  return ApiService.fetchData<T>({
    url: "/uesrs",
    method: "post",
    data,
  });
}

export async function apiGetCrmUserDetails<
  T,
  U extends Record<string, unknown>
>(params: U) {
  return ApiService.fetchData<T>({
    url: "/users/" + params.id,
    method: "get",
  });
}

//get login logs by user id
export async function apiGetLoginLogsByUserId<
  T,
  U extends Record<string, unknown>
>(data: U) {
  return ApiService.fetchData<T>({
    url: "/loginLog/user/" + data.id,
    method: "get",
  });
}
export async function apiDeleteCrmUser<T, U extends Record<string, unknown>>(
  data: U
) {
  return ApiService.fetchData<T>({
    url: "/crm/user/delete",
    method: "delete",
    data,
  });
}

export async function apiPutActivateUser<T, U extends Record<string, unknown>>(
  data: U
) {
  return ApiService.fetchData<T>({
    url: "/user/active/" + data.id,
    method: "put",
    data,
  });
}

export async function apiPutDeactivateUser<
  T,
  U extends Record<string, unknown>
>(data: U) {
  return ApiService.fetchData<T>({
    url: "/user/inactive/" + data.id,
    method: "put",
    data,
  });
}

///notifications/read/<id>
export async function apiPutNotificationRead<
  T,
  U extends Record<string, unknown>
>(id: U) {
  return ApiService.fetchData<T>({
    url: "/notifications/read/" + id,
    method: "put",
  });
}


//get notifications
export async function apiGetNotifications<T, U extends Record<string, unknown>>(
  data: U
) {
  return ApiService.fetchData<T>({
    url: "/notifications",
    method: "get",
    data,
  });
}
