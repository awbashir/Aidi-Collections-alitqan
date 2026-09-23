import { useEffect, useCallback, useMemo, useState, useRef } from "react";
import { useTranslation } from "react-i18next";
import {
  setTableData,
  useAppDispatch,
  useAppSelector,
  getPortfolios,
  setFilterData,
  setSearchOpen,
  setSearchClose,
} from "./store";
import DataTable from "@/components/shared/DataTable";
import { Button, Dropdown } from "@/components/ui";
import { HiOutlineArchive, HiOutlineSearch, HiOutlineTable } from "react-icons/hi";
import { NumericFormat } from "react-number-format";
import ExportExcel from "@/components/shared/myComponents/exportClientArchive";
import InquiryClintByExcel from "@/components/shared/myComponents/modelForm_V2";
import cloneDeep from "lodash/cloneDeep";
import ActionColumn from "./components/ActionColumn";
import MoveFromArchiveByExcel from "@/components/shared/myComponents/modelFormMoveFromArchiveByExcel";
import StatusPill from "@/components/shared/StatusPill";
import { useLocation } from "react-router-dom";
import { listQueryString } from "@/services/v2";
import { BulkMenu } from "@/views/phase2/bulk/entry";
import type { BulkLaunch } from "@/views/phase2/bulk/shared";

const TableOptions = ({ selectedIds = [] }: { selectedIds?: string[] }) => {
  const { t } = useTranslation();
  const dispatch = useAppDispatch();
  const [selectedInquiryClintByExcel, setSelectedInquiryClintByExcel] =
    useState([]);
  const pramas = useAppSelector((state) => state.archiveList.data.filterData);
  const search = useAppSelector((state) => state.archiveList.data.search);
  const sort = useAppSelector((state) => state.archiveList.data.tableData.sort);
  const tableState = useAppSelector((state) => state.archiveList.data.tableData);
  const location = useLocation();

  /* Phase 2: reviewed restore gets the selected rows, or this list's current
   * filter exactly as the list sends it (list "archive"; paging is dropped). */
  const restoreLaunch = (): BulkLaunch => ({
    from: { path: `/archive${location.hash || ""}`, labelKey: "p2.back.archive" },
    caseIds: selectedIds,
    filter: {
      list: "archive",
      query: listQueryString("/clints/archive/", {
        ...pramas,
        sort: tableState.sort?.order,
        page: tableState.page,
        pageSize: tableState.pageSize,
      }),
      count: typeof tableState.total === "number" ? tableState.total : null,
    },
  });

  const onclick = () => {
    search ? dispatch(setSearchClose()) : dispatch(setSearchOpen());
  };
  const isInitialMount = useRef(true);

  useEffect(() => {
    if (isInitialMount.current) {
      isInitialMount.current = false; // Skip the first render
      return;
    }

    dispatch(
      setFilterData({
        ...pramas,
        selectedClients_Qkey: selectedInquiryClintByExcel,
        selectedClients_Jkey: "accountNumber",
        selectedClients_Type: "string",
      })
    );
  }, [selectedInquiryClintByExcel]);

  /* The Excel-driven bulk actions keep their own components — and therefore
   * their own dialogs, handlers and permissions — mounted exactly as before.
   * Only the trigger moves into the "عمليات ملف إكسل" menu, which clicks the
   * component's own (visually hidden) button. */
  const excelRefs = useRef<Record<string, HTMLDivElement | null>>({});
  const setExcelRef = (key: string) => (el: HTMLDivElement | null) => {
    excelRefs.current[key] = el;
  };
  const runExcelAction = (key: string) => () => {
    excelRefs.current[key]?.querySelector("button")?.click();
  };

  const excelActions: {
    key: string;
    label: string;
    icon: JSX.Element;
  }[] = [
    {
      key: "inquiry",
      label: t("queryByExcel"),
      icon: <HiOutlineSearch />,
    },
    {
      key: "moveFromArchive",
      label: `${t("archive.moveFromArchive")} (${t("p2.entry.classic")})`,
      icon: <HiOutlineArchive />,
    },
  ];

  return (
    <div className="mb-4 flex flex-wrap items-center gap-2">
      {/* Leading edge — search and filter affordances. */}
      <Button
        icon={<HiOutlineSearch />}
        variant="default"
        size="sm"
        onClick={onclick}
      >
        {t("search")}
      </Button>

      {selectedInquiryClintByExcel?.file_name && (
        <Button
          variant="default"
          className="!text-red-600 dark:!text-red-500"
          size="sm"
          onClick={() => setSelectedInquiryClintByExcel([])}
        >
          {t("cancelEnquiry")}
        </Button>
      )}

      {/* Trailing edge — actions. */}
      <div className="ms-auto flex flex-wrap items-center gap-2">
        <BulkMenu
          testId="p2-archive-bulk"
          title={t("p2.entry.restoreMenu")}
          groups={[
            {
              header: t("p2.entry.forCases", { n: selectedIds.length }),
              entries: [{ kind: "restore", launch: restoreLaunch(), testId: "p2-entry-restore" }],
            },
            {
              header: t("p2.entry.fromFile"),
              entries: [
                {
                  kind: "restore",
                  launch: { ...restoreLaunch(), source: "spreadsheet" as const },
                  label: t("p2.entry.file.restore"),
                  testId: "p2-entry-file-restore",
                },
              ],
            },
          ]}
        />
        <ExportExcel
          pramas={{ ...pramas, sort }}
          notificationFailedMessage={t("archive.failExport")}
          notificationTitle={t("archive.exportTitle")}
          notificationSuccessMessage={t("archive.successExport")}
        />

        <Dropdown
          placement="bottom-end"
          renderTitle={
            <Button variant="default" size="sm" icon={<HiOutlineTable />}>
              عمليات ملف إكسل
            </Button>
          }
        >
          {excelActions.map((action) => (
            <Dropdown.Item
              key={action.key}
              eventKey={action.key}
              onClick={runExcelAction(action.key)}
            >
              <span className="flex items-center gap-2">
                <span className="text-base text-gray-500">{action.icon}</span>
                <span>{action.label}</span>
              </span>
            </Dropdown.Item>
          ))}
        </Dropdown>
      </div>

      {/* The Excel dialogs stay mounted; only their triggers are hidden. */}
      <div className="hidden" aria-hidden="true">
        <div ref={setExcelRef("inquiry")}>
          <InquiryClintByExcel
            filter={setSelectedInquiryClintByExcel}
            notificationFailedMessage={t("uploadFail")}
            notificationTitle={t("uploadTitle")}
            notificationSuccessMessage={t("uploadSuccess")}
          />
        </div>
        <div ref={setExcelRef("moveFromArchive")}>
          <MoveFromArchiveByExcel
            notificationFailedMessage={t("uploadFail")}
            notificationTitle={t("uploadTitle")}
            notificationSuccessMessage={t("uploadSuccess")}
          />
        </div>
      </div>
    </div>
  );
};

const PortfoliosTable = () => {
  const { t } = useTranslation();
  const dispatch = useAppDispatch();
  const data = useAppSelector((state) => state.archiveList.data.list);
  const loading = useAppSelector((state) => state.archiveList.data.loading);
  const filterData = useAppSelector(
    (state) => state.archiveList.data.filterData
  );

  const defaultColumns = useMemo(
    () => [
      {
        header: t("portfolios.name"),
        accessorKey: "clints_portfolios_name",
        enableSorting: false,
        cell: (props) => props.row.original?.portfolios?.name || "",
      },
      {
        header: t("contracts.name"),
        accessorKey: "contracts_name",
        enableSorting: false,
        cell: (props) => props.row.original?.contract?.name || "",
      },
      {
        header: t("archive.archiveBy"),
        accessorKey: "clintArchive_user_id",
        enableSorting: false,
        cell: (props) => props.row.original?.clintArchive?.user?.name || "",
      },
      {
        header: t("archive.archiveReason"),
        accessorKey: "clintArchive_archivereasonid_name",
        enableSorting: false,
        cell: (props) => {
          const reason =
            props.row.original?.clintArchive?.archive_reason?.name || "";
          return reason ? <StatusPill tone="neutral">{reason}</StatusPill> : "";
        },
      },
      {
        header: t("archive.archiveDate"),
        accessorKey: "archive_create_at",
        cell: (props) => {
          const date = new Date(props.row.original?.clintArchive?.create_at);
          return `${date.getFullYear()}-${
            date.getMonth() + 1
          }-${date.getDate()}`;
        },
      },
      {
        header: t("users.name"),
        accessorKey: "user_name",
        enableSorting: false,
        cell: (props) => props.row.original?.user?.name || "",
      },
      {
        header: t("comments.status"),
        accessorKey: "comment_status_name",
        enableSorting: false,
        cell: (props) => props.row.original?.comment_status?.name || "",
      },
      {
        header: t("clients.accountNumber"),
        accessorKey: "clint_accountNumber",
        enableSorting: false,
        cell: (props) => {
          const value = props.row.original?.accountNumber;
          return value ? (
            <span
              dir="ltr"
              title={String(value)}
              className="inline-block max-w-[190px] truncate align-middle tabular-nums"
            >
              {value}
            </span>
          ) : (
            ""
          );
        },
      },
      {
        header: t("clients.name"),
        accessorKey: "clint_name",
        enableSorting: false,
        cell: (props) => props.row.original?.name || "",
      },
      {
        header: t("clients.identityNumber"),
        accessorKey: "clint_identityNumber",
        enableSorting: false,
        cell: (props) => {
          const value = props.row.original?.identityNumber;
          return value ? (
            <span
              dir="ltr"
              title={String(value)}
              className="inline-block max-w-[190px] truncate align-middle tabular-nums"
            >
              {value}
            </span>
          ) : (
            ""
          );
        },
      },
      {
        header: t("amount"),
        accessorKey: "clint_amount",
        meta: { isNumeric: true },
        cell: (props) => (
          <NumericFormat
            displayType="text"
            value={props.row.original?.amount || 0}
            thousandSeparator
            decimalScale={2}
            fixedDecimalScale
          />
        ),
      },
      {
        header: "",
        id: "action",
        cell: (props) => (
          <ActionColumn row={props.row.original} fetchadata={fetchData} />
        ),
      },
    ],
    [t]
  );

  const [columns] = useState(defaultColumns);

  const { page, pageSize, sort, query, total } = useAppSelector(
    (state) => state.archiveList.data.tableData
  );

  const fetchData = useCallback(() => {
    console.log("fetchData", page, pageSize, sort, query, filterData);
    dispatch(getPortfolios({ page, pageSize, sort, query, filterData }));
  }, [page, pageSize, sort, query, filterData, dispatch]);

  useEffect(() => {
    console.log("fetchData", page, pageSize, sort, query, filterData);

    fetchData();
  }, [fetchData, page, pageSize, sort, filterData]);

  const tableData = useMemo(
    () => ({ page, pageSize, sort, query, total }),
    [page, pageSize, sort, query, total]
  );

  const onPaginationChange = (page) => {
    const newTableData = cloneDeep(tableData);
    newTableData.page = page;
    dispatch(setTableData(newTableData));
  };

  const onSelectChange = (value) => {
    const newTableData = cloneDeep(tableData);
    newTableData.pageSize = Number(value);
    newTableData.page = 1;
    dispatch(setTableData(newTableData));
  };

  const onSort = (sort) => {
    const newTableData = cloneDeep(tableData);
    newTableData.sort = sort;
    dispatch(setTableData(newTableData));
  };

  /* Phase 2: row selection for the reviewed restore (the legacy screen had
   * none). Cleared whenever the page of data changes, like the clients list. */
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  useEffect(() => {
    setSelectedIds([]);
  }, [data]);
  const onRowSelect = (checked: boolean, row: { id: string }) =>
    setSelectedIds((prev) =>
      checked
        ? prev.includes(row.id)
          ? prev
          : [...prev, row.id]
        : prev.filter((id) => id !== row.id)
    );
  const onAllRowSelect = (checked: boolean, rows: { original: { id: string } }[]) =>
    setSelectedIds(checked ? rows.map((r) => r.original.id) : []);

  return (
    <div className="mt-5">
      <TableOptions selectedIds={selectedIds} />
      <DataTable
        selectable
        onCheckBoxChange={onRowSelect}
        onIndeterminateCheckBoxChange={onAllRowSelect}
        columns={columns}
        data={data}
        skeletonAvatarColumns={[0]}
        skeletonAvatarProps={{ width: 28, height: 28 }}
        loading={loading}
        pagingData={{
          total: tableData.total,
          page: tableData.page,
          pageSize: tableData.pageSize,
        }}
        pageSizes={[100, 500, 1000]}
        onPaginationChange={onPaginationChange}
        onSelectChange={onSelectChange}
        onSort={onSort}
      />
    </div>
  );
};

export default PortfoliosTable;
