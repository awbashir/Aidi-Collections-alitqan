import ApiService from "./ApiService";

export async function apiGetContracts<T, U extends Record<string, unknown>>(
  data: U
) {
  return ApiService.fetchData<T>({
    url: "/contracts",
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
  data: U
) {
  return ApiService.fetchData<T>({
    url: "/branches",
    method: "get",
    data,
  });
}

export async function apPutContract<T, U extends Record<string, unknown>>(
  data: U
) {
  return ApiService.fetchData<T>({
    url: "/contracts/" + data.id,
    method: "put",
    data,
  });
}

//post contract
export async function apiPostContract<T, U extends Record<string, unknown>>(
  data: U
) {
  return ApiService.fetchData<T>({
    url: "/contracts",
    method: "post",
    data,
  });
}

export async function apiGetContractsDetails<
  T,
  U extends Record<string, unknown>
>(params: U) {
  return ApiService.fetchData<T>({
    url: "/contracts/" + params.id,
    method: "get",
  });
}

export async function apiPutActivateContract<
  T,
  U extends Record<string, unknown>
>(data: U) {
  return ApiService.fetchData<T>({
    url: "/contract/activate/" + data.id,
    method: "put",
    data,
  });
}

export async function apiPutDeactivateContract<
  T,
  U extends Record<string, unknown>
>(data: U) {
  return ApiService.fetchData<T>({
    url: "/contract/deactivate/" + data.id,
    method: "put",
    data,
  });
}
