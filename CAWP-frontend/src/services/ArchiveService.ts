import ApiService from "./ApiService";
import { asyncImportConfig } from "./JobsService";
import type { AsyncImportOption } from "./JobsService";

export async function apiGetArchive<T, U extends Record<string, unknown>>(
  data: U
) {
  return ApiService.fetchData<T>({
    url: "/clints/archive/",
    method: "get",
    data,
    params: {
      ...data?.filterData,
      sort: data.sort.order,
      page: data.page,
      pageSize: data.pageSize,
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

export async function apiDeletePortfolios<T, U extends Record<string, unknown>>(
  id: U
) {
  return ApiService.fetchData<T>({
    url: "/portfolios/" + id,
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
      ...data?.filterData,
    },
  });
}

///api/file/upload/ByExcel

export async function apiUploadFile<T extends Record<string, unknown>>(
  data: Record<string, unknown>
) {
  return ApiService.fetchData<T>({
    url: "/api/v2/file/upload/ByExcel",
    timeout: 0, // bulk Excel upload/export: may legitimately exceed the default timeout
    method: "post",
    data,
    headers: {
      "Content-Type": "multipart/form-data",
    },
  });
}

// PUT /clint/archivebyexcel — { account_number: <column header>, data: <rows>, archive_reason_id, user_id }
export async function apiArchiveByExcel<T extends Record<string, unknown>>(
  data: Record<string, unknown>,
  options?: AsyncImportOption
) {
  return ApiService.fetchData<T>({
    url: "/clint/archivebyexcel",
    method: "put",
    data,
    ...asyncImportConfig(options),
  });
}

///clint/unarchivebyexcel

export async function apiUnarchiveByExcel<T extends Record<string, unknown>>(
  data: Record<string, unknown>,
  options?: AsyncImportOption
) {
  return ApiService.fetchData<T>({
    url: "/clint/unarchivebyexcel",
    method: "put",
    data,
    ...asyncImportConfig(options),
  });
}

//  "/clint/unarchive/" + id

export async function apiUnarchive<T extends Record<string, unknown>>(
  id: Record<string, unknown>
) {
  return ApiService.fetchData<T>({
    url: "/clint/unarchive/" + id,
    method: "put",
  });
}
