import ApiService from "./ApiService";
import { asyncImportConfig } from "./JobsService";
import type { AsyncImportOption } from "./JobsService";

export async function apiGetClients<T, U extends Record<string, unknown>>(
  data: U,
) {
  return ApiService.fetchData<T>({
    url: "/clints",
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

export async function apiGetClients2<T, U extends Record<string, unknown>>(
  data: U,
) {
  return ApiService.fetchData<T>({
    url: "/v2/clints",
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

export async function apPutClient<T, U extends Record<string, unknown>>(
  data: U,
) {
  return ApiService.fetchData<T>({
    url: "/clints/" + data.id,
    method: "put",
    data,
  });
}

export async function apiGetClientsDetails<
  T,
  U extends Record<string, unknown>,
>(id: U) {
  return ApiService.fetchData<T>({
    url: "/clints/" + id,
    method: "get",
  });
}

export async function apiGetListClintContactTypes<
  T,
  U extends Record<string, unknown>,
>(data: U) {
  return ApiService.fetchData<T>({
    url: "/clint_contact_types",
    method: "get",
    data,
  });
}

export async function apiPostClintContactTypes<
  T,
  U extends Record<string, unknown>,
>(data: U) {
  return ApiService.fetchData<T>({
    url: "/clint_contacts",
    method: "post",
    data,
  });
}

//post /comments

export async function apiPostComments<T, U extends Record<string, unknown>>(
  data: U,
) {
  return ApiService.fetchData<T>({
    url: "/comments",
    method: "post",
    data,
  });
}

//get /comments

export async function apiGetComments<T, U extends Record<string, unknown>>(
  id: U,
) {
  return ApiService.fetchData<T>({
    url: "/comments/clint/" + id,
    method: "get",
  });
}

//DELETE COMMENTS
export async function apiDeleteComments<T, U extends Record<string, unknown>>(
  id: U,
) {
  return ApiService.fetchData<T>({
    url: "/comments/" + id,
    method: "delete",
  });
}

//delete clint_contact
export async function apiDeleteClintContact<
  T,
  U extends Record<string, unknown>,
>(id: U) {
  return ApiService.fetchData<T>({
    url: "/clint_contact/" + id,
    method: "delete",
  });
}
///clint_contact/default/<id>
export async function apiPutClintContactDefaultByID<
  T,
  U extends Record<string, unknown>,
>(id: U) {
  return ApiService.fetchData<T>({
    url: "/clint_contact/default/" + id,
    method: "put",
  });
}

///clints/delinquency/<id> put
export async function apiPutClintsDelinquencyByID<
  T,
  U extends Record<string, unknown>,
>(data: U) {
  return ApiService.fetchData<T>({
    url: "/clints/delinquency/" + data.clint_id,
    method: "put",
    data,
  });
}

///clint_contact/unset_default/<id>
export async function apiPutClintContactUnsetDefaultByID<
  T,
  U extends Record<string, unknown>,
>(id: U) {
  return ApiService.fetchData<T>({
    url: "/clint_contact/unset_default/" + id,
    method: "put",
  });
}

export async function apiGetPaymentsClientsTotal<
  T,
  U extends Record<string, unknown>,
>(id: U) {
  return ApiService.fetchData<T>({
    url: "/payments/clint/" + id + "/total",
    method: "get",
  });
}

export async function apiGetContactsIdentityNumber<
  T,
  U extends Record<string, unknown>,
>(id: U) {
  return ApiService.fetchData<T>({
    url: "/contacts/identityNumber/" + id,
    method: "get",
  });
}

export async function apiDeleteClients<T, U extends Record<string, unknown>>(
  id: U,
) {
  return ApiService.fetchData<T>({
    url: "/clint/" + id,
    method: "delete",
  });
}

//delete clints

export async function apiDeleteClintsByIDs<
  T,
  U extends Record<string, unknown>,
>(ids: U) {
  return ApiService.fetchData<T>({
    url: "/clints",
    method: "delete",
    data: { clints_ids: ids },
  });
}

export async function apiGetContactsMethods<
  T,
  U extends Record<string, unknown>,
>(id: U) {
  return ApiService.fetchData<T>({
    url: "/contact_methods",
    method: "get",
  });
}

export async function apiGetCommentReachability<
  T,
  U extends Record<string, unknown>,
>(data: U) {
  return ApiService.fetchData<T>({
    url: "/comment_reachability",
    method: "get",
    data,
  });
}

export async function apiGetCommentStatus<T, U extends Record<string, unknown>>(
  data: U,
) {
  return ApiService.fetchData<T>({
    url: "/comment_status",
    method: "get",
    data,
  });
}

//get collection_types

export async function apiGetCollectionTypes<
  T,
  U extends Record<string, unknown>,
>(data: U) {
  return ApiService.fetchData<T>({
    url: "/collection_types",
    method: "get",
    data,
  });
}

//get collection_methods

export async function apiGetCollectionMethods<
  T,
  U extends Record<string, unknown>,
>(data: U) {
  return ApiService.fetchData<T>({
    url: "/collection_methods",
    method: "get",
    data,
  });
}

//post /payments

export async function apiPostPayments<T, U extends Record<string, unknown>>(
  data: U,
) {
  return ApiService.fetchData<T>({
    url: "/payment",
    method: "post",
    data,
  });
}

///  "/payments/clint/" + id

export async function apiGetPayments<T, U extends Record<string, unknown>>(
  id: U,
) {
  return ApiService.fetchData<T>({
    url: "/payments/clint/" + id,
    method: "get",
  });
}

// post payment_date

export async function apiPostPaymentDate<T, U extends Record<string, unknown>>(
  data: U,
) {
  return ApiService.fetchData<T>({
    url: "/payment_date",
    method: "post",
    data,
  });
}

//get "/payment_date/clint/" + id,

export async function apiGetPaymentDate<T, U extends Record<string, unknown>>(
  id: U,
) {
  return ApiService.fetchData<T>({
    url: "/payment_date/clint/" + id,
    method: "get",
  });
}

//get "/sepcifyusers/clint/" + id

export async function apiGetSpecifyUsers<T, U extends Record<string, unknown>>(
  id: U,
) {
  return ApiService.fetchData<T>({
    url: "/sepcifyusers/clint/" + id,
    method: "get",
  });
}

//post /clint/archive/ + id

export async function apiPutClintArchiveByID<
  T,
  U extends Record<string, unknown>,
>(data: U) {
  return ApiService.fetchData<T>({
    url: "/clint/archive/" + data.id,
    method: "put",
    data,
  });
}

//put /clint/archive/ids

export async function apiPutClintArchiveByIDs<
  T,
  U extends Record<string, unknown>,
>(data: U) {
  return ApiService.fetchData<T>({
    url: "/clint/archive/ids",
    method: "put",
    data,
  });
}

export async function apiGetBranches<T, U extends Record<string, unknown>>(
  data: U,
) {
  return ApiService.fetchData<T>({
    url: "/branches",
    method: "get",
    data,
  });
}

export async function apiGetUsers<T, U extends Record<string, unknown>>(
  data: U,
) {
  return ApiService.fetchData<T>({
    url: "/uesrs",
    method: "get",
    data,
  });
}

//post /sepcifyusers/ids

export async function apiPostSpecifyUsersIdsOne<
  T,
  U extends Record<string, unknown>,
>(data: U) {
  return ApiService.fetchData<T>({
    url: "/sepcifyusers/ids",
    method: "post",
    data,
  });
}
//put /clint/lock/<id>
export async function apiPutClintLockByID<T, U extends Record<string, unknown>>(
  id: U,
  reason?: string,
) {
  return ApiService.fetchData<T>({
    url: "/clint/lock/" + id,
    method: "put",
    data: { reason },
  });
}

//put /clint/unlock/<id>
export async function apiPutClintUnlockByID<
  T,
  U extends Record<string, unknown>,
>(id: U, reason?: string) {
  return ApiService.fetchData<T>({
    url: "/clint/unlock/" + id,
    method: "put",
    data: { reason },
  });
}

//put '/clints/bulk-lock'
export async function apiPutClintsBulkLock<
  T,
  U extends Record<string, unknown>,
>(data: U) {
  return ApiService.fetchData<T>({
    url: "/clints/bulk-lock",
    method: "put",
    data,
  });
}

//put '/clints/bulk-unlock'
export async function apiPutClintsBulkUnlock<
  T,
  U extends Record<string, unknown>,
>(data: U) {
  return ApiService.fetchData<T>({
    url: "/clints/bulk-unlock",
    method: "put",
    data,
  });
}
//GET /clint/lock-history/{id}
export async function apiGetLockHistory<T, U extends Record<string, unknown>>(
  id: U,
) {
  return ApiService.fetchData<T>({
    url: "/clint/lock-history/" + id,
    method: "get",
  });
}

// /cdr

export async function apiGetContracts<T, U extends Record<string, unknown>>(
  id: U,
) {
  return ApiService.fetchData<T>({
    url: "/contracts",
    method: "get",
  });
}

export async function apiGetPortfolios<T, U extends Record<string, unknown>>(
  id: U,
) {
  return ApiService.fetchData<T>({
    url: "/portfolios",
    method: "get",
  });
}

///send/notfication
export async function apiPostSendNotification<
  T,
  U extends Record<string, unknown>,
>(data: U) {
  return ApiService.fetchData<T>({
    url: "/send/notification",
    method: "post",
    data,
  });
}

//post /clint/addClintsByExcel

export async function apiPostClintAddClintsByExcel<
  T,
  U extends Record<string, unknown>,
>(data: U, options?: AsyncImportOption) {
  return ApiService.fetchData<T>({
    url: "/clint/addClintsByExcel",
    method: "post",
    data,
    ...asyncImportConfig(options),
  });
}

//post /clint/SepcifyUsersByExcel

export async function apiPostClintSepcifyUsersByExcel<
  T,
  U extends Record<string, unknown>,
>(data: U, options?: AsyncImportOption) {
  return ApiService.fetchData<T>({
    url: "/clint/SepcifyUsersByExcel",
    method: "post",
    data,
    ...asyncImportConfig(options),
  });
}

export async function apiUploadFile<T extends Record<string, unknown>>(
  data: Record<string, unknown>,
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

export async function apiUploadFile2<T extends Record<string, unknown>>(
  data: Record<string, unknown>,
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

//get /clients/export

export async function apiGetClientsExport<T, U extends Record<string, unknown>>(
  data: U,
) {
  return ApiService.fetchData<T>({
    url: "/clients/export",
    timeout: 0, // bulk Excel upload/export: may legitimately exceed the default timeout
    method: "get",
    data,
    params: {
      ...data?.filterData,
    },
  });
}

//get /updateSides

export async function apiGetUpdateSides<T, U extends Record<string, unknown>>(
  data: U,
) {
  return ApiService.fetchData<T>({
    url: "/updateSides",
    method: "get",
    data,
  });
}

//post /clints/update/byexcel

export async function apiPostClintsUpdateByExcel<
  T,
  U extends Record<string, unknown>,
>(data: U, options?: AsyncImportOption) {
  return ApiService.fetchData<T>({
    url: "/clints/update/byexcel",
    method: "post",
    data,
    ...asyncImportConfig(options),
  });
}

export async function apiPostClintsAttributeByExcel<
  T,
  U extends Record<string, unknown>,
>(data: U, options?: AsyncImportOption) {
  return ApiService.fetchData<T>({
    url: "/clints/attribute/byexcel",
    method: "post",
    data,
    ...asyncImportConfig(options),
  });
}

export async function apiPostcommentsByExcel<
  T,
  U extends Record<string, unknown>,
>(data: U, options?: AsyncImportOption) {
  return ApiService.fetchData<T>({
    url: "/commentsbyexcel",
    method: "post",
    data,
    ...asyncImportConfig(options),
  });
}

export async function apiGetCdr<T, U extends Record<string, unknown>>(data: U) {
  return ApiService.fetchData<T>({
    url: "/cdr",
    method: "get",
    data,
  });
}

export async function apiGetCdrByDate<T, U extends Record<string, unknown>>(
  data: U,
) {
  return ApiService.fetchData<T>({
    url: "/cdr_by_date",
    method: "get",
    params: data,
  });
}

///cdr/recording/recording_path
export async function apiGetCdrRecordingPath<
  T,
  U extends Record<string, unknown>,
>(data: U) {
  return ApiService.fetchData<T>({
    url: "/cdr/recording",
    method: "get",
    params: data,
    responseType: "blob",
  });
}

// ---- Clint Documents ----

// post /clint/<id>/documents  (multipart/form-data)
export async function apiUploadClintDocument<T extends Record<string, unknown>>(
  id: string,
  formData: FormData,
) {
  return ApiService.fetchData<T>({
    url: "/clint/" + id + "/documents",
    method: "post",
    data: formData,
    headers: {
      "Content-Type": "multipart/form-data",
    },
  });
}

// get /clint/<id>/documents
export async function apiGetClintDocuments<
  T,
  U extends Record<string, unknown>,
>(id: U) {
  return ApiService.fetchData<T>({
    url: "/clint/" + id + "/documents",
    method: "get",
  });
}

// delete /clint/document/<doc_id>
export async function apiDeleteClintDocument<
  T,
  U extends Record<string, unknown>,
>(id: U) {
  return ApiService.fetchData<T>({
    url: "/clint/document/" + id,
    method: "delete",
  });
}

// get /clint/document/<doc_id>/download  (blob, requires auth header)
export async function apiDownloadClintDocument<
  T,
  U extends Record<string, unknown>,
>(id: U) {
  return ApiService.fetchData<T>({
    url: "/clint/document/" + id + "/download",
    method: "get",
    responseType: "blob",
  });
}
