import ApiService from "./ApiService";

export async function apiGetPortfolios<T, U extends Record<string, unknown>>(
  data: U
) {
  return ApiService.fetchData<T>({
    url: "/supervisors",
    method: "get",
    data,
    params: {
      ...data,
      field: data.sort.key,
      sort: data.sort.order,
      "customeFilterStatusesValue[]":
        data.filterData.customeFilterStatusesValue,
      "customeFilterArchivingStatusesValue[]":
        data.filterData.customeFilterArchivingStatusesValue,
    },
  });
}

export async function apiGetBranches<T, U extends Record<string, unknown>>(
  id: U
) {
  return ApiService.fetchData<T>({
    url: "/branches",
    method: "get",
  });
}

export async function apiGetUsers<T, U extends Record<string, unknown>>(id: U) {
  return ApiService.fetchData<T>({
    url: "/uesrs",
    method: "get",
  });
}

export async function apiGetContracts<T, U extends Record<string, unknown>>(
  id: U
) {
  return ApiService.fetchData<T>({
    url: "/contracts",
    method: "get",
  });
}

export async function apiGetArchiveReasons<
  T,
  U extends Record<string, unknown>
>(data: U) {
  return ApiService.fetchData<T>({
    url: "/clints/archive/reson",
    method: "get",
    data,
  });
}

export async function apPutPortfolio<T, U extends Record<string, unknown>>(
  data: U
) {
  return ApiService.fetchData<T>({
    url: "/supervisor/" + data.supervisor_id,
    method: "put",
    data,
  });
}

export async function apiGetPortfoliosDetails<
  T,
  U extends Record<string, unknown>
>(params: U) {
  return ApiService.fetchData<T>({
    url: "/supervisor/" + params.id,
    method: "get",
  });
}

export async function apiDeletePortfolios<T, U extends Record<string, unknown>>(
  id: U
) {
  return ApiService.fetchData<T>({
    url: "/supervisor/" + id,
    method: "delete",
  });
}

export async function apiPostSupervisor<T, U extends Record<string, unknown>>(
  data: U
) {
  return ApiService.fetchData<T>({
    url: "/supervisors",
    method: "post",
    data,
  });
}

// post /supervisor_contract

export async function apiPostSupervisorContract<
  T,
  U extends Record<string, unknown>
>(data: U) {
  return ApiService.fetchData<T>({
    url: "/supervisor_contract",
    method: "post",
    data,
  });
}

//post supervisor_user

export async function apiPostSupervisorUser<
  T,
  U extends Record<string, unknown>
>(data: U) {
  return ApiService.fetchData<T>({
    url: "/supervisor_user",
    method: "post",
    data,
  });
}
