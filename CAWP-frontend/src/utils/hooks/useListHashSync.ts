/**
 * `useClientsListHashSync`, generalised for the other legacy list slices that
 * keep `{ tableData: { page, pageSize, sort, query }, filterData }`.
 *
 *   URL -> slice   on mount (when the hash carries list keys) and on every
 *                  hash change (Back/Forward, a pasted link). Render the list
 *                  only once `ready` is true, so its first request already
 *                  uses the restored state.
 *   slice -> URL   whenever page, page size, sort, query or the filters
 *                  change. The first write on a visit replaces the entry.
 *
 * Only the filters named in `filterKeys` go into the hash; every other key in
 * `filterData` (spreadsheet inquiry lists and the like) is left alone.
 * See `views/clients/list/hooks/useClientsHashState.ts` for the reference.
 */
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import type { AnyAction } from "@reduxjs/toolkit";
import { useAppDispatch } from "@/store";
import useHashState, {
  hashCodec,
  hashStatesEqual,
  type HashField,
  type HashSchema,
} from "@/utils/hooks/useHashState";

type Sort = { key: string | number; order: "asc" | "desc" | "" };

export type ListHashBase = {
  page: number;
  pageSize: number;
  sort: Sort;
  query: string | undefined;
};

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyRecord = Record<string, any>;

/** page / size / sort / q, the part every list shares. */
export function listHashBaseSchema(
  pageSizes: readonly number[],
  defaultPageSize: number
): HashSchema<ListHashBase> {
  return {
    page: {
      key: "page",
      codec: hashCodec.int({ min: 1, max: 1_000_000 }),
      default: 1,
    },
    pageSize: {
      key: "size",
      codec: hashCodec.oneOf<number>(pageSizes),
      default: defaultPageSize,
    },
    sort: { key: "sort", codec: hashCodec.sort(), default: { key: "", order: "" } },
    query: {
      key: "q",
      codec: hashCodec.text({ maxLength: 200 }),
      default: undefined,
      history: "replace",
    },
  };
}

const BASE_KEYS = ["page", "pageSize", "sort", "query"] as const;

export type ListHashSyncOptions<F extends AnyRecord> = {
  /** Base fields plus one field per hash-owned filter. Module level. */
  schema: HashSchema<ListHashBase & F>;
  /** Hash filter name -> the `filterData` key it maps to. */
  filterKeys: { [K in keyof F]: string };
  tableData: AnyRecord;
  filterData: AnyRecord;
  setTableData: (payload: AnyRecord) => AnyAction;
  setFilterData: (payload: AnyRecord) => AnyAction;
};

/** Keeps a list slice and the URL hash in sync. Returns `ready`. */
function useListHashSync<F extends AnyRecord>({
  schema,
  filterKeys,
  tableData,
  filterData,
  setTableData,
  setFilterData,
}: ListHashSyncOptions<F>): boolean {
  type S = ListHashBase & F;
  const dispatch = useAppDispatch();
  const { state: fromUrl, present, hash, write } = useHashState(schema);

  const [ready, setReady] = useState(false);
  const firstWrite = useRef(true);

  const filterNames = Object.keys(filterKeys) as Array<keyof F & string>;
  const field = (name: keyof S) =>
    (schema as Record<keyof S, HashField<unknown>>)[name];
  const same = (name: keyof S, a: unknown, b: unknown) =>
    field(name).codec.serialize(a) === field(name).codec.serialize(b);

  const toHash = (table: AnyRecord, filters: AnyRecord): S => {
    const state = {
      page: table?.page ?? 1,
      pageSize: table?.pageSize ?? field("pageSize").default,
      sort: table?.sort ?? { key: "", order: "" },
      query: table?.query || undefined,
    } as AnyRecord;
    for (const name of filterNames) state[name] = filters?.[filterKeys[name]];
    return state as S;
  };

  // Latest values for effects that must not re-run when they change.
  const latest = useRef({ tableData, filterData, fromUrl, toHash });
  latest.current = { tableData, filterData, fromUrl, toHash };

  const applyToStore = useCallback(
    (next: S) => {
      const { tableData: table, filterData: filters, toHash: build } =
        latest.current;
      const current = build(table, filters);
      if (hashStatesEqual(schema, current, next)) return;

      // Dispatch only the half that changed: a new `filterData` object alone
      // makes the list fetch again.
      if (BASE_KEYS.some((k) => !same(k, current[k], next[k]))) {
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
      if (filterNames.some((k) => !same(k, current[k], next[k]))) {
        const nextFilters: AnyRecord = { ...filters };
        for (const name of filterNames) {
          if (next[name] !== undefined) {
            nextFilters[filterKeys[name]] = next[name];
          } else {
            delete nextFilters[filterKeys[name]];
          }
        }
        dispatch(setFilterData(nextFilters));
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [dispatch, schema]
  );

  // URL -> slice, before the list's first fetch effect runs.
  useLayoutEffect(() => {
    if (!ready) {
      if (present) applyToStore(latest.current.fromUrl);
      setReady(true);
      return;
    }
    applyToStore(latest.current.fromUrl);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hash]);

  // slice -> URL.
  const { page, pageSize, sort, query } = tableData ?? {};
  useEffect(() => {
    if (!ready) return;
    const next = latest.current.toHash(
      latest.current.tableData,
      latest.current.filterData
    );
    write(next, firstWrite.current ? { replace: true } : undefined);
    firstWrite.current = false;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, page, pageSize, sort, query, filterData]);

  return ready;
}

export default useListHashSync;
