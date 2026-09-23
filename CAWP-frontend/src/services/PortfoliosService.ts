import ApiService from "./ApiService";

export async function apiGetPortfolios<T, U extends Record<string, unknown>>(
  data: U,
) {
  return ApiService.fetchData<T>({
    url: "/portfolios",
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
      customeFilterPortfolioCreateAtFromValue:
        data.filterData.customeFilterPortfolioCreateAtFromValue,
      customeFilterPortfolioCreateAtToValue:
        data.filterData.customeFilterPortfolioCreateAtToValue,
    },
  });
}

export async function apiGetArchiveReasons<
  T,
  U extends Record<string, unknown>,
>(data: U) {
  return ApiService.fetchData<T>({
    url: "/clints/archive/reson",
    method: "get",
    data,
  });
}

export async function apPutPortfolio<T, U extends Record<string, unknown>>(
  data: U,
) {
  return ApiService.fetchData<T>({
    url: "/portfolio/" + data.id,
    method: "put",
    data,
  });
}

export async function apiputPortfoliolsToArchive<
  T,
  U extends Record<string, unknown>,
>(data: U) {
  return ApiService.fetchData<T>({
    url: "/clint/archive/portofolio/" + data.id,
    method: "put",
    data,
  });
}

export async function apiGetPortfoliolsExport<
  T,
  U extends Record<string, unknown>,
>(data: U) {
  return ApiService.fetchData<T>({
    url: "/portfolios/export",
    timeout: 0, // bulk Excel upload/export: may legitimately exceed the default timeout
    method: "get",
    data,
    params: {
      ...data?.filterData,
    },
  });
}

export async function apiGetPortfoliosDetails<
  T,
  U extends Record<string, unknown>,
>(params: U) {
  return ApiService.fetchData<T>({
    url: "/portfolio/" + params.id,
    method: "get",
  });
}

export async function apiDeletePortfolios<T, U extends Record<string, unknown>>(
  id: U,
) {
  return ApiService.fetchData<T>({
    url: "/portfolios/" + id,
    method: "delete",
  });
}
