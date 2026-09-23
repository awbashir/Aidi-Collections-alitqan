/**
 * Reference integration of `useHashState`: the clients (cases) list.
 *
 * The list keeps its state in the `clientsList` Redux slice, and the table
 * fetches from that slice. This hook keeps the slice and the URL hash in step:
 *
 *   URL -> slice   on mount, and whenever the hash changes (Back/Forward, a
 *                  pasted link). The table renders only once the hash has been
 *                  applied, so its first request already carries the restored
 *                  state: one request, same shape as before.
 *   slice -> URL   whenever page, page size, sort, query or the applied
 *                  filters change. The first write on a visit replaces the
 *                  current entry; later ones push (the query replaces).
 *
 * What is in the hash is exactly what the table sends: the applied filters,
 * not the half-filled search panel. The spreadsheet inquiry list
 * (`customeFilterInquiryClintByExcelValue`) stays out: it comes from a local
 * file and can hold thousands of account numbers.
 */
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import useHashState, {
  hashCodec,
  hashStatesEqual,
  type HashSchema,
} from "@/utils/hooks/useHashState";
import {
  initialFilterData,
  initialTableData,
  setFilterData,
  setTableData,
  useAppDispatch,
  useAppSelector,
} from "../store";

type Id = string | number;
type Sort = { key: string | number; order: "asc" | "desc" | "" };

export type ClientsListHashState = {
  page: number;
  pageSize: number;
  sort: Sort;
  query: string | undefined;
  contract: Id[] | undefined;
  portfolio: Id[] | undefined;
  commentStatus: Id[] | undefined;
  branch: Id[] | undefined;
  user: Id[] | undefined;
  attr: string | undefined;
  from: string | undefined;
  to: string | undefined;
  /** The case last opened from the list; its row is marked on return. */
  caseId: Id | undefined;
};

/** The page sizes the table's pager offers. */
export const CLIENTS_PAGE_SIZES = [100, 500, 1000] as const;

export const clientsListHashSchema: HashSchema<ClientsListHashState> = {
  page: {
    key: "page",
    codec: hashCodec.int({ min: 1, max: 1_000_000 }),
    default: 1,
  },
  pageSize: {
    key: "size",
    codec: hashCodec.oneOf<number>(CLIENTS_PAGE_SIZES),
    default: initialTableData.pageSize as number,
  },
  sort: {
    key: "sort",
    codec: hashCodec.sort(),
    default: { key: "", order: "" },
  },
  query: {
    key: "q",
    codec: hashCodec.text({ maxLength: 200 }),
    default: undefined,
    history: "replace",
  },
  contract: { key: "contract", codec: hashCodec.idList(), default: undefined },
  portfolio: {
    key: "portfolio",
    codec: hashCodec.idList(),
    default: undefined,
  },
  commentStatus: {
    key: "status",
    codec: hashCodec.idList(),
    default: undefined,
  },
  branch: { key: "branch", codec: hashCodec.idList(), default: undefined },
  user: { key: "user", codec: hashCodec.idList(), default: undefined },
  attr: {
    key: "attr",
    codec: hashCodec.text({ maxLength: 200 }),
    default: undefined,
  },
  from: { key: "from", codec: hashCodec.date(), default: undefined },
  to: { key: "to", codec: hashCodec.date(), default: undefined },
  caseId: {
    key: "case",
    codec: hashCodec.id(),
    default: undefined,
    history: "replace",
  },
};

/** Filter keys the hash owns, and the slice key each one maps to. */
const FILTER_KEYS = {
  contract: "customeFilterContractValue",
  portfolio: "customeFilterPortfoliosValue",
  commentStatus: "customeFilterCommentStatusValue",
  branch: "customeFilterBranchesValue",
  user: "customeFilterUserValue",
  attr: "customeFilterAttributeValue",
  from: "customeFilterPortfolioCreateAtFromValue",
  to: "customeFilterPortfolioCreateAtToValue",
} as const;

type FilterName = keyof typeof FILTER_KEYS;

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyRecord = Record<string, any>;

/** Slice state -> hash state (the case id is carried over separately). */
export function toClientsHashState(
  tableData: AnyRecord,
  filterData: AnyRecord,
  caseId: Id | undefined
): ClientsListHashState {
  const filters = {} as Record<FilterName, unknown>;
  for (const name of Object.keys(FILTER_KEYS) as FilterName[]) {
    filters[name] = filterData?.[FILTER_KEYS[name]];
  }
  return {
    page: tableData?.page ?? 1,
    pageSize: tableData?.pageSize ?? 100,
    sort: tableData?.sort ?? { key: "", order: "" },
    query: tableData?.query || undefined,
    ...(filters as Pick<ClientsListHashState, FilterName>),
    caseId,
  };
}

/** How many hash-tracked filters are applied (for the "filters applied" chip). */
export function countAppliedClientFilters(filterData: AnyRecord): number {
  return (Object.keys(FILTER_KEYS) as FilterName[]).filter((name) => {
    const field = clientsListHashSchema[name];
    const text = field.codec.serialize(
      filterData?.[FILTER_KEYS[name]] as never
    );
    return text !== "";
  }).length;
}

/** Filter data with every hash-owned filter cleared; other keys kept. */
export function clearHashFilters(filterData: AnyRecord): AnyRecord {
  return {
    ...initialFilterData,
    customeFilterInquiryClintByExcelValue:
      filterData?.customeFilterInquiryClintByExcelValue ??
      initialFilterData.customeFilterInquiryClintByExcelValue,
  };
}

/**
 * Keeps the clients list slice and the URL hash in sync.
 * Returns `ready`: render the table only when it is true.
 */
export function useClientsListHashSync(): boolean {
  const dispatch = useAppDispatch();
  const tableData = useAppSelector((state) => state.clientsList.data.tableData);
  const filterData = useAppSelector(
    (state) => state.clientsList.data.filterData
  );

  const {
    state: fromUrl,
    present,
    hash,
    write,
    read,
  } = useHashState(clientsListHashSchema);

  const [ready, setReady] = useState(false);
  const firstWrite = useRef(true);

  // Latest values for effects that must not re-run when they change.
  const latest = useRef({ tableData, filterData, fromUrl });
  latest.current = { tableData, filterData, fromUrl };

  const applyToStore = useCallback(
    (next: ClientsListHashState) => {
      const { tableData: table, filterData: filters } = latest.current;
      const current = toClientsHashState(table, filters, next.caseId);
      if (hashStatesEqual(clientsListHashSchema, current, next)) return;

      const tablePart = (s: ClientsListHashState) => ({
        ...s,
        ...Object.fromEntries(
          (Object.keys(FILTER_KEYS) as FilterName[]).map((k) => [k, undefined])
        ),
      });
      // Dispatch only the half that changed: a new `filterData` object alone
      // makes the table fetch again.
      if (
        !hashStatesEqual(
          clientsListHashSchema,
          tablePart(current),
          tablePart(next)
        )
      ) {
        dispatch(
          setTableData({
            ...table,
            page: next.page,
            pageSize: next.pageSize,
            sort: next.sort,
            query: next.query ?? "",
          })
        );
      }
      const filtersChanged = (Object.keys(FILTER_KEYS) as FilterName[]).some(
        (name) => {
          const codec = clientsListHashSchema[name].codec;
          return (
            codec.serialize(current[name] as never) !==
            codec.serialize(next[name] as never)
          );
        }
      );
      if (filtersChanged) {
        const nextFilters: AnyRecord = clearHashFilters(filters);
        for (const name of Object.keys(FILTER_KEYS) as FilterName[]) {
          if (next[name] !== undefined) {
            nextFilters[FILTER_KEYS[name]] = next[name];
          }
        }
        dispatch(setFilterData(nextFilters));
      }
    },
    [dispatch]
  );

  // URL -> slice. A layout effect so the restored state is in the store
  // before the table's first fetch effect runs.
  useLayoutEffect(() => {
    if (!ready) {
      // First visit with no list keys in the hash: keep whatever the
      // slice holds (the existing in-app behaviour) and write it below.
      if (present) applyToStore(latest.current.fromUrl);
      setReady(true);
      return;
    }
    applyToStore(latest.current.fromUrl);
    // Only a hash change (Back/Forward, a pasted link) re-applies.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hash]);

  // slice -> URL. Depends on the slice only, never on the parsed hash, so a
  // Back navigation is applied to the slice before anything is written.
  const { page, pageSize, sort, query } = tableData ?? {};
  useEffect(() => {
    if (!ready) return;
    const next = toClientsHashState(
      latest.current.tableData,
      latest.current.filterData,
      read().caseId
    );
    write(next, firstWrite.current ? { replace: true } : undefined);
    firstWrite.current = false;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, page, pageSize, sort, query, filterData]);

  return ready;
}

/** The selected (last opened) case, read from and written to the hash. */
export function useClientsSelectedCase() {
  const { state, setState } = useHashState(clientsListHashSchema);
  const select = useCallback(
    (caseId: Id | undefined) => setState({ caseId }, { replace: true }),
    [setState]
  );
  return { caseId: state.caseId, select };
}
