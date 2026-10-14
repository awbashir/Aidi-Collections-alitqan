import {
  forwardRef,
  useMemo,
  useRef,
  useEffect,
  useState,
  useImperativeHandle,
} from "react";
import classNames from "classnames";
import Table from "@/components/ui/Table";
import Pagination from "@/components/ui/Pagination";
import Select from "@/components/ui/Select";
import Checkbox from "@/components/ui/Checkbox";
import TableRowSkeleton from "./loaders/TableRowSkeleton";
import Loading from "./Loading";
import StatusPill from "./StatusPill";
import { statusTint } from "./statusColor";
import {
  useReactTable,
  getCoreRowModel,
  getFilteredRowModel,
  getPaginationRowModel,
  getSortedRowModel,
  flexRender,
  ColumnDef,
  ColumnSort,
  Row,
  CellContext,
} from "@tanstack/react-table";
import type { SkeletonProps } from "@/components/ui/Skeleton";
import type {
  ForwardedRef,
  ChangeEvent,
  CSSProperties,
  KeyboardEvent as ReactKeyboardEvent,
  MouseEvent as ReactMouseEvent,
} from "react";
import type { CheckboxProps } from "@/components/ui/Checkbox";
import TFoot from "../ui/Table/TFoot";
import { NumericFormat } from "react-number-format";
import { useTranslation } from "react-i18next";
import { useConfig } from "@/components/ui/ConfigProvider";
import { useNavigate } from "react-router-dom";
import {
  openCaseInApp,
  shouldIgnoreRowClick,
  type RowActivateEvent,
} from "@/utils/v2/rowNavigation";

const PriceAmount = ({ amount = 0 }: { amount?: number }) => {
  return (
    <NumericFormat
      displayType="text"
      value={(Math.round(amount * 100) / 100).toFixed(2)}
      thousandSeparator={true}
    />
  );
};

/* Totals row — 13.5px / 800 navy tabular on #FAFCFD. */
const TFootRows = ({ label, value, place }: TFootRowsProps) => {
  return (
    <Tr>
      <Td colSpan={place} />
      <Td className="!text-[13.5px] font-extrabold !text-navy-600 dark:!text-gray-100">
        {label}
      </Td>
      <Td className="!text-[13.5px] font-extrabold !text-navy-600 dark:!text-gray-100 tabular-nums">
        <PriceAmount amount={value} />
      </Td>
    </Tr>
  );
};

export type OnSortParam = { order: "asc" | "desc" | ""; key: string | number };

type DataTableProps<T> = {
  columns: ColumnDef<T>[];
  data?: unknown[];
  loading?: boolean;
  onCheckBoxChange?: (checked: boolean, row: T) => void;
  onIndeterminateCheckBoxChange?: (checked: boolean, rows: Row<T>[]) => void;
  onPaginationChange?: (page: number) => void;
  onSelectChange?: (num: number) => void;
  onSort?: (sort: OnSortParam) => void;
  pageSizes?: number[];
  selectable?: boolean;
  skeletonAvatarColumns?: number[];
  skeletonAvatarProps?: SkeletonProps;
  pagingData?: {
    total: number;
    pageIndex?: number;
    page?: number;
    pageSize: number;
    displayTotal?: boolean;
  };
  /** Rows open their case (`clintID` path on the row) in the same tab;
   *  Ctrl/Cmd/Shift- or middle-click opens a new tab. */
  clickAbility?: boolean;
  clintID?: string;
  /** Called when a row is clicked (or Enter pressed on it) anywhere that is
   *  not a control inside it. Takes precedence over `clickAbility`. */
  onRowClick?: (row: T, event: RowActivateEvent) => void;
  /** Which rows react to clicks (default: all, when clicks are on). */
  isRowClickable?: (row: T) => boolean;
  /** Marks the row whose `id` matches (e.g. the case last opened from this
   *  list, restored from the URL hash) with a leading azure rail. */
  activeRowId?: string | number;
  /** Controlled sort display (e.g. a sort restored from the URL hash). When
   *  set, the header arrows follow it; clicks still report through `onSort`. */
  sortState?: OnSortParam;
  footerTotalAmount?: {
    status: boolean;
    label: string;
    value: number;
    place: number;
  };
};

type CheckBoxChangeEvent = ChangeEvent<HTMLInputElement>;

interface IndeterminateCheckboxProps extends Omit<CheckboxProps, "onChange"> {
  onChange: (event: CheckBoxChangeEvent) => void;
  indeterminate: boolean;
  onCheckBoxChange?: (event: CheckBoxChangeEvent) => void;
  onIndeterminateCheckBoxChange?: (event: CheckBoxChangeEvent) => void;
}

const { Tr, Th, Td, THead, TBody, Sorter } = Table;

/* A stable identity for "no rows yet". A `data = []` default parameter mints a
 * fresh array on every render, which re-fired the selection-reset effect below,
 * which writes table state, which renders again — that was the
 * `Maximum update depth exceeded` loop on /clients (the list slices have no
 * `list` key in their initial state, so `props.data` is undefined until the
 * first fetch resolves). */
const EMPTY_ROWS: unknown[] = [];

/* Columns whose value is a database-driven status name: the cell is rendered
 * as a pill in the row's own status colour instead of a bare string. */
const STATUS_COLUMN_IDS = ["comment_status_name", "status_name"];

/* TanStack derives a column id from `accessorKey` by swapping `.` for `_`, so
 * `comment_status..name` arrives as `comment_status__name`. */
const normaliseColumnId = (id: string) => id.replace(/[._]+/g, "_");

const IndeterminateCheckbox = (props: IndeterminateCheckboxProps) => {
  const {
    indeterminate,
    onChange,
    onCheckBoxChange,
    onIndeterminateCheckBoxChange,
    ...rest
  } = props;

  const ref = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (typeof indeterminate === "boolean" && ref.current) {
      ref.current.indeterminate = !rest.checked && indeterminate;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ref, indeterminate]);

  const handleChange = (e: CheckBoxChangeEvent) => {
    onChange(e);
    onCheckBoxChange?.(e);
    onIndeterminateCheckBoxChange?.(e);
  };

  return (
    <Checkbox
      ref={ref}
      className="mb-0"
      onChange={(_, e) => handleChange(e)}
      {...rest}
    />
  );
};

export type DataTableResetHandle = {
  resetSorting: () => void;
  resetSelected: () => void;
};

function _DataTable<T>(
  props: DataTableProps<T>,
  ref: ForwardedRef<DataTableResetHandle>
) {
  const {
    skeletonAvatarColumns,
    columns: columnsProp = [],
    data = EMPTY_ROWS,
    loading = false,
    onCheckBoxChange,
    onIndeterminateCheckBoxChange,
    onPaginationChange,
    onSelectChange,
    onSort,
    pageSizes = [10, 25, 50, 100],
    selectable = false,
    skeletonAvatarProps,
    pagingData = {
      total: 0,
      pageIndex: 1,
      pageSize: 10,
    },
    clickAbility = false,
    clintID = "id",
    onRowClick,
    isRowClickable,
    activeRowId,
    sortState,
    footerTotalAmount = { status: false, label: "", value: 0, place: 0 },
  } = props;

  const { t } = useTranslation();
  const { mode } = useConfig();
  const navigate = useNavigate();
  const scheme = mode === "dark" ? "dark" : "light";

  const { pageSize, total } = pagingData;
  /* Most call sites spell the current page `page`; a couple use `pageIndex`. */
  const pageIndex = pagingData.pageIndex ?? pagingData.page ?? 1;

  const [sorting, setSorting] = useState<ColumnSort[] | null>(null);

  const displaySorting: ColumnSort[] | null = sortState
    ? sortState.key !== "" && sortState.order
      ? [{ id: String(sortState.key), desc: sortState.order === "desc" }]
      : []
    : sorting;

  const pageSizeOption = useMemo(
    () =>
      pageSizes.map((number) => ({
        value: number,
        label: `${number}`,
      })),
    [pageSizes]
  );

  const handleCheckBoxChange = (checked: boolean, row: T) => {
    if (!loading) {
      onCheckBoxChange?.(checked, row);
    }
  };

  const handleIndeterminateCheckBoxChange = (
    checked: boolean,
    rows: Row<T>[]
  ) => {
    if (!loading) {
      onIndeterminateCheckBoxChange?.(checked, rows);
    }
  };

  const handlePaginationChange = (page: number) => {
    if (!loading) {
      onPaginationChange?.(page);
    }
  };

  const handleSelectChange = (value?: number) => {
    if (!loading) {
      onSelectChange?.(Number(value));
    }
  };

  useEffect(() => {
    if (Array.isArray(sorting)) {
      const sortOrder =
        sorting.length > 0 ? (sorting[0].desc ? "desc" : "asc") : "";
      const id = sorting.length > 0 ? sorting[0].id : "";
      onSort?.({ order: sortOrder, key: id });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sorting]);

  const finalColumns: ColumnDef<T>[] = useMemo(() => {
    const columns = columnsProp;

    if (selectable) {
      return [
        {
          id: "select",
          size: 44,
          header: ({ table }) => (
            <IndeterminateCheckbox
              checked={table.getIsAllRowsSelected()}
              indeterminate={table.getIsSomeRowsSelected()}
              onChange={table.getToggleAllRowsSelectedHandler()}
              onIndeterminateCheckBoxChange={(e) => {
                handleIndeterminateCheckBoxChange(
                  e.target.checked,
                  table.getRowModel().rows
                );
              }}
            />
          ),
          cell: ({ row }) => (
            <IndeterminateCheckbox
              checked={row.getIsSelected()}
              disabled={!row.getCanSelect()}
              indeterminate={row.getIsSomeSelected()}
              onChange={row.getToggleSelectedHandler()}
              onCheckBoxChange={(e) =>
                handleCheckBoxChange(e.target.checked, row.original)
              }
            />
          ),
        },
        ...columns,
      ];
    }
    return columns;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [columnsProp, selectable]);

  const table = useReactTable({
    data,
    // eslint-disable-next-line  @typescript-eslint/no-explicit-any
    columns: finalColumns as ColumnDef<unknown | object | any[], any>[],
    getCoreRowModel: getCoreRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    getSortedRowModel: getSortedRowModel(),
    manualPagination: true,
    manualSorting: true,
    onSortingChange: (sorter) => {
      // Controlled: toggle from what the header shows, not the internal copy.
      if (sortState && typeof sorter === "function") {
        setSorting(sorter(displaySorting ?? []) as ColumnSort[]);
        return;
      }
      setSorting(sorter as ColumnSort[]);
    },
    state: {
      sorting: displaySorting as ColumnSort[],
    },
  });

  const resetSorting = () => {
    table.resetSorting();
  };

  const resetSelected = () => {
    table.toggleAllRowsSelected(false);
  };

  /* Clear the selection when the page of rows changes. Guarded: TanStack's
   * `toggleAllRowsSelected` always writes a new state object, so an unguarded
   * call re-renders — and would loop if `data` ever changed identity per
   * render. Resetting an empty selection is a no-op anyway. */
  useEffect(() => {
    if (Object.keys(table.getState().rowSelection).length > 0) {
      resetSelected();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data]);

  useImperativeHandle(ref, () => ({
    resetSorting,
    resetSelected,
  }));

  /* Row clicks: the case id for `clickAbility`, read from the `clintID`
   * path (e.g. "clint.id"). */
  const rowCaseId = (original: T): unknown =>
    clintID
      .split(".")
      // eslint-disable-next-line  @typescript-eslint/no-explicit-any
      .reduce((acc: any, prop) => acc?.[prop], original);

  const canClickRow = (original: T): boolean => {
    if (!onRowClick && !clickAbility) return false;
    if (isRowClickable && !isRowClickable(original)) return false;
    if (onRowClick) return true;
    const id = rowCaseId(original);
    return id !== undefined && id !== null && id !== "";
  };

  const activateRow = (original: T, event: RowActivateEvent) => {
    if (!canClickRow(original) || shouldIgnoreRowClick(event)) return;
    if (onRowClick) {
      onRowClick(original, event);
      return;
    }
    openCaseInApp(navigate, String(rowCaseId(original)), event);
  };

  const rows = table.getRowModel().rows;
  const columnCount = (finalColumns as Array<T>).length;
  const isEmpty = !loading && data.length === 0;

  /* "الصفوف 1–100 من 171,452" — leading edge of the footer, never shrinks. */
  const rangeFrom = total === 0 ? 0 : (pageIndex - 1) * pageSize + 1;
  const rangeTo = Math.min(pageIndex * pageSize, total || 0);
  const nf = (n: number) => new Intl.NumberFormat("en-US").format(n || 0);

  return (
    <Loading loading={loading && data.length !== 0} type="cover">
      <Table>
        <THead>
          {table.getHeaderGroups().map((headerGroup) => (
            <Tr key={headerGroup.id}>
              {headerGroup.headers.map((header) => {
                const canSort = header.column.getCanSort();
                const sorted = header.column.getIsSorted();
                const meta = header.column.columnDef.meta as
                  | { isNumeric?: boolean }
                  | undefined;
                return (
                  <Th
                    key={header.id}
                    colSpan={header.colSpan}
                    className={classNames(
                      "whitespace-nowrap",
                      header.id === "select" && "w-11",
                      meta?.isNumeric && "text-end"
                    )}
                  >
                    {header.isPlaceholder ? null : (
                      <div
                        className={classNames(
                          "inline-flex items-center gap-1.5",
                          canSort && "cursor-pointer select-none point",
                          loading && "pointer-events-none"
                        )}
                        onClick={header.column.getToggleSortingHandler()}
                      >
                        {flexRender(
                          header.column.columnDef.header,
                          header.getContext()
                        )}
                        {canSort && (
                          <span
                            className={classNames(
                              "inline-flex items-center text-[12px] leading-none [&_svg]:!text-current",
                              sorted ? "text-azure-500" : "text-gray-500"
                            )}
                          >
                            <Sorter sort={sorted} />
                          </span>
                        )}
                      </div>
                    )}
                  </Th>
                );
              })}
            </Tr>
          ))}
        </THead>
        {loading && data.length === 0 ? (
          <TableRowSkeleton
            // eslint-disable-next-line  @typescript-eslint/no-explicit-any
            columns={columnCount}
            rows={Math.min(pagingData.pageSize || 10, 12)}
            avatarInColumns={skeletonAvatarColumns}
            avatarProps={skeletonAvatarProps}
          />
        ) : (
          <TBody>
            {isEmpty && (
              <Tr>
                <Td
                  colSpan={columnCount || 1}
                  className="!h-[132px] text-center !text-[13.5px] !text-gray-600"
                >
                  {t("noData")}
                </Td>
              </Tr>
            )}
            {rows.slice(0, pageSize).map((row) => {
              /* The DB-driven status colour becomes a 3px leading rail plus a
               * 10% tint — never a painted row. Selected rows keep the azure
               * rail and tint that _tables.css owns. */
              // eslint-disable-next-line  @typescript-eslint/no-explicit-any
              const original = row.original as any;
              const rawColor =
                original?.comment_status?.color || original?.status?.color;
              const selected = row.getIsSelected();
              const current =
                activeRowId !== undefined &&
                activeRowId !== null &&
                activeRowId !== "" &&
                String(original?.id) === String(activeRowId);
              const tone = rawColor ? statusTint(rawColor, scheme) : null;
              const rowData = row.original as T;
              const clickable = canClickRow(rowData);

              return (
                <Tr
                  onClick={
                    clickable
                      ? (e: RowActivateEvent) => activateRow(rowData, e)
                      : undefined
                  }
                  onAuxClick={
                    clickable
                      ? (e: ReactMouseEvent<HTMLElement>) => {
                          if (e.button === 1) activateRow(rowData, e);
                        }
                      : undefined
                  }
                  onKeyDown={
                    clickable
                      ? (e: ReactKeyboardEvent<HTMLElement>) => {
                          if (e.key === "Enter" && e.target === e.currentTarget) {
                            activateRow(rowData, e);
                          }
                        }
                      : undefined
                  }
                  tabIndex={clickable ? 0 : undefined}
                  key={row.id}
                  className={classNames(
                    selected && "table-row-selected",
                    current && "table-row-current",
                    clickable &&
                      "cursor-pointer focus-visible:outline focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-azure-500"
                  )}
                  aria-current={current ? "true" : undefined}
                  style={
                    tone && !selected
                      ? { backgroundColor: tone.tint }
                      : undefined
                  }
                >
                  {row.getVisibleCells().map((cell, cellIndex) => {
                    const meta = cell.column.columnDef.meta as
                      | { isNumeric?: boolean; isId?: boolean }
                      | undefined;
                    const isStatusCell =
                      !!tone &&
                      STATUS_COLUMN_IDS.includes(
                        normaliseColumnId(cell.column.id)
                      );
                    const rendered = flexRender(
                      cell.column.columnDef.cell,
                      cell.getContext()
                    );

                    return (
                      <Td
                        key={cell.id}
                        /* The selection and row-action cells are controls,
                           even between their buttons. */
                        data-no-row-click={
                          cell.column.id === "select" ||
                          cell.column.id === "action"
                            ? ""
                            : undefined
                        }
                        className={classNames(
                          cell.column.id === "select" && "w-11",
                          meta?.isNumeric && "text-end tabular-nums",
                          meta?.isId && "table-id-cell",
                          /* 3px status rail on the leading edge — the sign
                             flips under RTL. */
                          cellIndex === 0 &&
                            tone &&
                            !selected &&
                            "ltr:shadow-[inset_3px_0_0_var(--row-rail)] rtl:shadow-[inset_-3px_0_0_var(--row-rail)]"
                        )}
                        style={
                          cellIndex === 0 && tone && !selected
                            ? ({
                                "--row-rail": tone.rail,
                              } as CSSProperties)
                            : undefined
                        }
                      >
                        {isStatusCell ? (
                          <StatusPill color={rawColor}>{rendered}</StatusPill>
                        ) : (
                          rendered
                        )}
                      </Td>
                    );
                  })}
                </Tr>
              );
            })}
          </TBody>
        )}
        {footerTotalAmount.status && (
          <TFoot>
            <TFootRows
              label={footerTotalAmount.label}
              value={footerTotalAmount.value}
              place={footerTotalAmount.place}
            />
          </TFoot>
        )}
      </Table>
      {/* Footer — range label on the leading edge, pager + page size on the
          trailing edge, on a #FAFCFD strip with a 1px top border. */}
      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-gray-300 dark:border-night-border bg-gray-50 dark:bg-night-page px-5 py-3">
        <div
          className="shrink-0 whitespace-nowrap text-[12.5px] text-gray-600 dark:text-night-muted tabular-nums"
          style={{ flex: "0 0 auto" }}
        >
          {t("pagination.range", {
            from: nf(rangeFrom),
            to: nf(rangeTo),
            total: nf(total),
          })}
        </div>
        <div className="flex items-center gap-2">
          <Pagination
            pageSize={pageSize}
            currentPage={pageIndex}
            total={total}
            onChange={handlePaginationChange}
            displayTotal={false}
          />
          <div style={{ minWidth: 96 }}>
            <Select
              size="sm"
              menuPlacement="top"
              isSearchable={false}
              value={pageSizeOption.filter(
                (option) => option.value === pageSize
              )}
              options={pageSizeOption}
              onChange={(option) => handleSelectChange(option?.value)}
            />
          </div>
        </div>
      </div>
    </Loading>
  );
}

const DataTable = forwardRef(_DataTable) as <T>(
  props: DataTableProps<T> & {
    ref?: ForwardedRef<DataTableResetHandle>;
  }
) => ReturnType<typeof _DataTable>;

export type { ColumnDef, Row, CellContext };
export default DataTable;
