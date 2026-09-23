import ApiService from "./ApiService";

export async function apiGetListArchiveReason<
  T,
  U extends Record<string, unknown>
>(data: U) {
  return ApiService.fetchData<T>({
    url: "/clints/archive/reson",
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

export async function apiPostArchiveReason<
  T,
  U extends Record<string, unknown>
>(data: U) {
  return ApiService.fetchData<T>({
    url: "/clints/archive/reson",
    method: "post",
    data,
  });
}

//apiGetArchiveReasonsDetails
export async function apiGetArchiveReasonsDetails<
  T,
  U extends Record<string, unknown>
>(data: U) {
  return ApiService.fetchData<T>({
    url: "/clints/archive/reson/" + data.id,
    method: "get",
  });
}

// put archive reasons

export async function apiPutArchiveReasons<
  T,
  U extends Record<string, unknown>
>(data: U) {
  return ApiService.fetchData<T>({
    url: "/clints/archive/reson/" + data.id,
    method: "put",
    data,
  });
}

//get apiGetListCollectionMethods
export async function apiGetListCollectionMethods<
  T,
  U extends Record<string, unknown>
>(data: U) {
  return ApiService.fetchData<T>({
    url: "/collection_methods",
    method: "get",
    data,
    params: {
      ...data,
      field: data.sort.key,
      sort: data.sort.order,
    },
  });
}

//apiPostCollectionMethods

export async function apiPostCollectionMethods<
  T,
  U extends Record<string, unknown>
>(data: U) {
  return ApiService.fetchData<T>({
    url: "/collection_methods",
    method: "post",
    data,
  });
}

//apiGetCollectionMethodsDetails
export async function apiGetCollectionMethodsDetails<
  T,
  U extends Record<string, unknown>
>(data: U) {
  return ApiService.fetchData<T>({
    url: "/collection_methods/" + data.id,
    method: "get",
  });
}

// put collection methods

export async function apiPutCollectionMethods<
  T,
  U extends Record<string, unknown>
>(data: U) {
  return ApiService.fetchData<T>({
    url: "/collection_methods/" + data.id,
    method: "put",
    data,
  });
}

///nationalities

export async function apiGetListNationalities<
  T,
  U extends Record<string, unknown>
>(data: U) {
  return ApiService.fetchData<T>({
    url: "/nationalities",
    method: "get",
    data,
    params: {
      ...data,
      field: data.sort.key,
      sort: data.sort.order,
    },
  });
}

export async function apiPostNationalities<
  T,
  U extends Record<string, unknown>
>(data: U) {
  return ApiService.fetchData<T>({
    url: "/nationalities",
    method: "post",
    data,
  });
}

export async function apiGetNationalitiesDetails<
  T,
  U extends Record<string, unknown>
>(data: U) {
  return ApiService.fetchData<T>({
    url: "/nationalities/" + data.id,
    method: "get",
  });
}

export async function apiPutNationalities<T, U extends Record<string, unknown>>(
  data: U
) {
  return ApiService.fetchData<T>({
    url: "/nationalities/" + data.id,
    method: "put",
    data,
  });
}

//updateSides

export async function apiGetListUpdateSides<
  T,
  U extends Record<string, unknown>
>(data: U) {
  return ApiService.fetchData<T>({
    url: "/updateSides",
    method: "get",
    data,
    params: {
      ...data,
      field: data.sort.key,
      sort: data.sort.order,
    },
  });
}

export async function apiPostUpdateSides<T, U extends Record<string, unknown>>(
  data: U
) {
  return ApiService.fetchData<T>({
    url: "/updateSides",
    method: "post",
    data,
  });
}

export async function apiGetUpdateSidesDetails<
  T,
  U extends Record<string, unknown>
>(data: U) {
  return ApiService.fetchData<T>({
    url: "/updateSides/" + data.id,
    method: "get",
  });
}

export async function apiPutUpdateSides<T, U extends Record<string, unknown>>(
  data: U
) {
  return ApiService.fetchData<T>({
    url: "/updateSides/" + data.id,
    method: "put",
    data,
  });
}

//comment_status

export async function apiGetListroles<T, U extends Record<string, unknown>>(
  data: U
) {
  return ApiService.fetchData<T>({
    url: "/roles",
    method: "get",
    data,
    params: {
      ...data,
      field: data.sort.key,
      sort: data.sort.order,
    },
  });
}

export async function apiPostRoles<T, U extends Record<string, unknown>>(
  data: U
) {
  return ApiService.fetchData<T>({
    url: "/roles",
    method: "post",
    data,
  });
}

export async function apiGetRolesDetails<T, U extends Record<string, unknown>>(
  data: U
) {
  return ApiService.fetchData<T>({
    url: "/roles/" + data.id,
    method: "get",
  });
}

export async function apiPutRoles<T, U extends Record<string, unknown>>(
  data: U
) {
  return ApiService.fetchData<T>({
    url: "/roles/" + data.id,
    method: "put",
    data,
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

export async function apiGetCommentReachability<
  T,
  U extends Record<string, unknown>
>(data: U) {
  return ApiService.fetchData<T>({
    url: "/comment_reachability",
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
