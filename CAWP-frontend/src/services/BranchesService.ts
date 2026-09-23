import ApiService from "./ApiService";

export async function apiGetPortfolios<T, U extends Record<string, unknown>>(
  data: U
) {
  return ApiService.fetchData<T>({
    url: "/branches",
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
    url: "/branches/" + data.id,
    method: "put",
    data,
  });
}

export async function apiGetPortfoliosDetails<
  T,
  U extends Record<string, unknown>
>(params: U) {
  return ApiService.fetchData<T>({
    url: "/branches/" + params.id,
    method: "get",
  });
}

export async function apiDeletePortfolios<T, U extends Record<string, unknown>>(
  id: U
) {
  return ApiService.fetchData<T>({
    url: "/portfolios/" + id,
    method: "delete",
  });
}

export async function apiPostBranch<T, U extends Record<string, unknown>>(
  data: U
) {
  return ApiService.fetchData<T>({
    url: "/branches",
    method: "post",
    data,
  });
}
