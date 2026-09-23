import ApiService from "./ApiService";

export async function apiGetArchive<T, U extends Record<string, unknown>>(
  data: U
) {
  return ApiService.fetchData<T>({
    url: "/payments/approval_status",
    method: "get",
    data,
    params: {
      ...data?.filterData,
      field: data.sort.key,
      sort: data.sort.order,
      page: data.page,
      pageSize: data.pageSize,
    },
  });
}

//apiGetClientsArchiveExport

export async function apiGetpaymentsExport<
  T,
  U extends Record<string, unknown>
>(data: U) {
  return ApiService.fetchData<T>({
    url: "/payments/approval_status/export",
    timeout: 0, // bulk Excel upload/export: may legitimately exceed the default timeout
    method: "get",
    data,
    params: {
      ...data?.filterData,
      field: data?.sort?.key,
      sort: data?.sort?.order,
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
    url: "/portfolio/" + data.id,
    method: "put",
    data,
  });
}

export async function apiGetPortfoliosDetails<
  T,
  U extends Record<string, unknown>
>(params: U) {
  return ApiService.fetchData<T>({
    url: "/portfolio/" + params.id,
    method: "get",
  });
}

export async function apiDeletePayment<T, U extends Record<string, unknown>>(
  id: U
) {
  return ApiService.fetchData<T>({
    url: "/payments/" + id,
    method: "delete",
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

export async function apiGetPortfolios<T, U extends Record<string, unknown>>(
  id: U
) {
  return ApiService.fetchData<T>({
    url: "/portfolios",
    method: "get",
  });
}

export async function apiGetCommentReachability<
  T,
  U extends Record<string, unknown>
>(data: U) {
  return ApiService.fetchData<T>({
    url: "/comment_reachability",
    method: "get",
    data,
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

export async function apiGetCollectionMethods<
  T,
  U extends Record<string, unknown>
>(data: U) {
  return ApiService.fetchData<T>({
    url: "/collection_methods",
    method: "get",
    data,
  });
}

export async function apiGetCollectionTypes<
  T,
  U extends Record<string, unknown>
>(data: U) {
  return ApiService.fetchData<T>({
    url: "/collection_types",
    method: "get",
    data,
  });
}

export async function apiGetCommentStatus<T, U extends Record<string, unknown>>(
  data: U
) {
  return ApiService.fetchData<T>({
    url: "/comment_status",
    method: "get",
    data,
  });
}

export async function apiGetUsers<T, U extends Record<string, unknown>>(
  data: U
) {
  return ApiService.fetchData<T>({
    url: "/uesrs",
    method: "get",
    data,
  });
}

//get /clients/archive/export

export async function apiGetClientsArchiveExport<
  T,
  U extends Record<string, unknown>
>(data: U) {
  return ApiService.fetchData<T>({
    url: "/clients/archive/export",
    timeout: 0, // bulk Excel upload/export: may legitimately exceed the default timeout
    method: "get",
    data,
    params: {
      ...data,
    },
  });
}

///api/file/upload/ByExcel

export async function apiUploadFile<T extends Record<string, unknown>>(
  data: Record<string, unknown>
) {
  return ApiService.fetchData<T>({
    url: "/api/file/upload/ByExcel",
    timeout: 0, // bulk Excel upload/export: may legitimately exceed the default timeout
    method: "post",
    data,
    headers: {
      "Content-Type": "multipart/form-data",
    },
  });
}

///clint/unarchivebyexcel

export async function apiUnarchiveByExcel<T extends Record<string, unknown>>(
  data: Record<string, unknown>
) {
  return ApiService.fetchData<T>({
    url: "/clint/unarchivebyexcel",
    timeout: 0, // bulk Excel upload/export: may legitimately exceed the default timeout
    method: "put",
    data,
  });
}

//  "/clint/unarchive/" + id

export async function apiEditPayment<T extends Record<string, unknown>>(
  data: Record<string, unknown>
) {
  return ApiService.fetchData<T>({
    url: "/payment/" + data.id,
    method: "put",
    data,
  });
}

export async function apiGetPaymentDetails<
  T,
  U extends Record<string, unknown>
>(id: U) {
  return ApiService.fetchData<T>({
    url: "/payment/" + id,
    method: "get",
  });
}
