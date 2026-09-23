import { useEffect, useCallback, useMemo, useState, useRef } from "react";
import { useTranslation } from "react-i18next";
import {
  setTableData,
  useAppDispatch,
  useAppSelector,
  getClients,
  deactivateClient,
  activateClient,
  Client,
  setSearchOpen,
  setSearchClose,
  setFilterData,
  setSelectedRow,
  deleteClintsByIDs,
} from "./store";
import DataTable from "@/components/shared/DataTable";
import { Link } from "react-router-dom";
import { Button, Dropdown } from "@/components/ui";
import {
  HiPlusCircle,
  HiDownload,
  HiOutlineSearch,
  HiOutlineTable,
  HiOutlineUserAdd,
  HiOutlineArchive,
  HiOutlineRefresh,
  HiOutlineAdjustments,
  HiOutlineChatAlt2,
  HiOutlineBell,
} from "react-icons/hi";
import { NumericFormat } from "react-number-format";
import AddClientsByExcelButton from "./components/AddClientsButton";
import SepcifyUsersByExcelButton from "./components/SepcifyUsersByExcelButton";
import EnquiryByExcelButton from "./components/EnquiryByExcelButton";
import UpdateDataByExcelButton from "./components/UpdateDataByExcelButton";
import UpdateAttributeByExcelButton from "./components/UpdateAttributeByExcelButton";
import CommentByExcelButton from "./components/commentbyExcelButton";
import DeleteSelectedButton from "./components/DeleteSelectedButton";
import SepcifyUsersSelectedButton from "./components/SepcifyUsersSelectedButton";
import MoveToArchiveSelectedButton from "./components/MoveToArchiveSelectedButton";
import MoveArchiveByExcelButton from "./components/MoveArchiveByExcelButton";
import InquiryClintByExcel from "@/components/shared/myComponents/modelForm_V2";
import SendNotification from "@/components/shared/myComponents/sendNotification";

import cloneDeep from "lodash/cloneDeep";

import ActionColumn from "./components/ActionColumn";
import ExportByExcel from "./components/export";
import { useFetchClients } from "./hooks/GetClientHook";
import LockUnlockSelectedButton from "./components/LockUnlockSelectedButton";

const TableOptions = () => {
  const { t } = useTranslation();
  const dispatch = useAppDispatch();
  const [selectedInquiryClintByExcel, setSelectedInquiryClintByExcel] =
    useState([]);
  const pramas = useAppSelector((state) => state.clientsList.data.filterData);

  const search = useAppSelector((state) => state.clientsList.data.search);

  const selectedRow = useAppSelector(
    (state) => state.clientsList.data.selectedRow,
  );

  const onclick = () => {
    search ? dispatch(setSearchClose()) : dispatch(setSearchOpen());
  };

  const firstRender = useRef(true);

  useEffect(() => {
    if (firstRender.current) {
      firstRender.current = false;
      return; // Prevent first execution
    }
    dispatch(
      setFilterData({
        ...pramas,

        customeFilterInquiryClintByExcelValue: selectedInquiryClintByExcel,

        //-----------------
      }),
    );
  }, [selectedInquiryClintByExcel]);

  const { permissions } = useAppSelector((state) => state.auth.user);
  const perm = (permissions ?? {}) as Record<string, boolean>;

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
    visible: boolean;
  }[] = [
    {
      key: "specifyUsers",
      label: t("clients.SepcifyClientToUserByExcel"),
      icon: <HiOutlineUserAdd />,
      visible: !!perm.clints_sepcifyUser,
    },
    {
      key: "moveArchive",
      label: t("clients.moveArchiveByExcel"),
      icon: <HiOutlineArchive />,
      visible: !!perm.clints_UpdateByExcel,
    },
    {
      key: "inquiry",
      label: t("queryByExcel"),
      icon: <HiOutlineSearch />,
      visible: true,
    },
    {
      key: "notify",
      label: t("sendNotification"),
      icon: <HiOutlineBell />,
      visible: !!perm.clints_sepcifyUser,
    },
    {
      key: "updateData",
      label: t("updateByExcel"),
      icon: <HiOutlineRefresh />,
      visible: !!perm.clints_UpdateByExcel,
    },
    {
      key: "updateAttribute",
      label: t("clients.updateAttributeByExcel"),
      icon: <HiOutlineAdjustments />,
      visible: !!perm.clints_UpdateByExcel,
    },
    {
      key: "comments",
      label: t("addcommentsByExcel"),
      icon: <HiOutlineChatAlt2 />,
      visible: !!perm.clints_UpdateByExcel,
    },
  ];

  const visibleExcelActions = excelActions.filter((a) => a.visible);

  return (
    <div className="mb-4 flex flex-wrap items-center gap-2">
      {/* Leading edge — search and filter affordances. */}
      <Button
        icon={<HiOutlineSearch />}
        variant="default"
        size="sm"
        onClick={onclick}
      >
        بحث
      </Button>

      {selectedInquiryClintByExcel.length > 0 && (
        <Button
          variant="default"
          className="!text-red-600 dark:!text-red-500"
          size="sm"
          onClick={() => setSelectedInquiryClintByExcel([])}
        >
          {t("cancelEnquiry")}
        </Button>
      )}

      {/* Trailing edge — actions. Exactly one primary. */}
      <div className="ms-auto flex flex-wrap items-center gap-2">
        <ExportByExcel
          pramas={pramas}
          notificationFailedMessage={t("clients.failExport")}
          notificationTitle={t("clients.exportTitle")}
          notificationSuccessMessage={t("clients.successExport")}
        />

        {perm.clints_sepcifyUser && (
          <SepcifyUsersSelectedButton ids={selectedRow} />
        )}
        {perm.clints_move_archive && (
          <MoveToArchiveSelectedButton ids={selectedRow} />
        )}
        {perm.clints_lock && (
          <LockUnlockSelectedButton ids={selectedRow} isLocking={true} />
        )}
        {perm.clints_lock && (
          <LockUnlockSelectedButton ids={selectedRow} isLocking={false} />
        )}
        {perm.clints_delete && (
          <DeleteSelectedButton
            row={selectedRow}
            notificationTitle={"clients.deletes"}
            notificationFailedMessage={"clients.deletesFail"}
            notificationSuccessMessage={"clients.deletesSuccess"}
            confirmDialogTitle={"clients.deletes"}
            confirmDialogMessage={"clients.confirmDeletes"}
            api={deleteClintsByIDs}
          />
        )}

        {visibleExcelActions.length > 0 && (
          <Dropdown
            placement="bottom-end"
            renderTitle={
              <Button variant="default" size="sm" icon={<HiOutlineTable />}>
                عمليات ملف إكسل
              </Button>
            }
          >
            {visibleExcelActions.map((action) => (
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
        )}

        {perm.clints_add && (
          <AddClientsByExcelButton
            notificationTitle={t("uploadExcel")}
            notificationSuccessMessage={t("uploadSuccess")}
            notificationFailedMessage={t("uploadFail")}
          />
        )}
      </div>

      {/* The Excel dialogs stay mounted; only their triggers are hidden. */}
      <div className="hidden" aria-hidden="true">
        {perm.clints_sepcifyUser && (
          <div ref={setExcelRef("specifyUsers")}>
            <SepcifyUsersByExcelButton
              notificationTitle={t("uploadExcel")}
              notificationSuccessMessage={t("uploadSuccess")}
              notificationFailedMessage={t("uploadFail")}
            />
          </div>
        )}
        {perm.clints_UpdateByExcel && (
          <div ref={setExcelRef("moveArchive")}>
            <MoveArchiveByExcelButton
              notificationTitle={t("uploadExcel")}
              notificationSuccessMessage={t("uploadSuccess")}
              notificationFailedMessage={t("uploadFail")}
            />
          </div>
        )}
        <div ref={setExcelRef("inquiry")}>
          <InquiryClintByExcel
            filter={setSelectedInquiryClintByExcel}
            notificationTitle={t("uploadExcel")}
            notificationSuccessMessage={t("uploadSuccess")}
            notificationFailedMessage={t("uploadFail")}
          />
        </div>
        {perm.clints_sepcifyUser && (
          <div ref={setExcelRef("notify")}>
            <SendNotification
              filter={setSelectedInquiryClintByExcel}
              notificationTitle={t("uploadExcel")}
              notificationSuccessMessage={t("uploadSuccess")}
              notificationFailedMessage={t("uploadFail")}
            />
          </div>
        )}
        {perm.clints_UpdateByExcel && (
          <div ref={setExcelRef("updateData")}>
            <UpdateDataByExcelButton
              notificationTitle={t("uploadExcel")}
              notificationSuccessMessage={t("uploadSuccess")}
              notificationFailedMessage={t("uploadFail")}
            />
          </div>
        )}
        {perm.clints_UpdateByExcel && (
          <div ref={setExcelRef("updateAttribute")}>
            <UpdateAttributeByExcelButton
              notificationTitle={t("uploadExcel")}
              notificationSuccessMessage={t("uploadSuccess")}
              notificationFailedMessage={t("uploadFail")}
            />
          </div>
        )}
        {perm.clints_UpdateByExcel && (
          <div ref={setExcelRef("comments")}>
            <CommentByExcelButton
              notificationTitle={t("uploadExcel")}
              notificationSuccessMessage={t("uploadSuccess")}
              notificationFailedMessage={t("uploadFail")}
            />
          </div>
        )}
      </div>
    </div>
  );
};

const ClientsTable = () => {
  const { t } = useTranslation();
  const dispatch = useAppDispatch();
  const data = useAppSelector((state) => state.clientsList.data.list);
  const loading = useAppSelector((state) => state.clientsList.data.loading);
  const filterData = useAppSelector(
    (state) => state.clientsList.data.filterData,
  );

  const defaultColumns: ColumnDef<User>[] = useMemo(
    () => [
      {
        header: t("portfolios.name"),
        accessorKey: "portfolios..name",
        cell: (props) => {
          const { portfolios } = props.row.original;
          return portfolios ? portfolios.name : "";
        },
      },
      {
        header: t("portfolios_create_at"),
        accessorKey: "portfolios..create_at",
        enableSorting: false,
        cell: (props) => {
          const { portfolios } = props.row.original;
          return portfolios
            ? new Date(portfolios.create_at).toLocaleDateString()
            : "";
        },
      },
      {
        header: t("users.name"),
        accessorKey: "user..name",

        cell: (props) => {
          const { user } = props.row.original;
          return user ? user.name : "";
        },
      },
      {
        header: t("created_atCountDays"),
        accessorKey: "specifyUser..create_at",
        meta: { isNumeric: true },
        cell: (props) => {
          const { create_at } = props.row.original.specifyUser
            ? props.row.original.specifyUser
            : "";
          const date = new Date(create_at);
          const now = new Date();
          const diff = now.getTime() - date.getTime();
          const days = Math.floor(diff / (1000 * 60 * 60 * 24));

          return days ? days : 0;
        },
      },
      {
        header: t("comments.lastCommentDate"),
        accessorKey: "comment..create_at",
        cell: (props) => {
          const { create_at } = props.row.original.comment
            ? props.row.original.comment
            : "";
          return create_at ? new Date(create_at).toLocaleDateString() : "";
        },
      },
      {
        header: t("comments.status"),
        accessorKey: "comment_status..name",
        cell: (props) => {
          const { comment_status } = props.row.original;
          return comment_status ? comment_status.name : "";
        },
      },
      {
        header: t("contracts.contract"),
        accessorKey: "contract..id",
        cell: (props) => {
          const { contract } = props.row.original;
          return contract ? contract.name : "";
        },
      },
      {
        header: t("clients.accountNumber"),
        accessorKey: "accountNumber",
        cell: (props) => {
          const { accountNumber } = props.row.original;
          if (!accountNumber) {
            return "";
          }
          /* Latin identifier inside Arabic copy — force LTR, tabular, and one
             line with an ellipsis rather than four wrapped lines. */
          return (
            <span
              dir="ltr"
              title={String(accountNumber)}
              className="inline-block max-w-[190px] truncate align-middle tabular-nums"
            >
              {accountNumber}
            </span>
          );
        },
      },
      //amount
      {
        header: t("amountdebt"),
        accessorKey: "amount",
        meta: { isNumeric: true },
        cell: (props) => {
          const { amount } = props.row.original;
          return (
            <NumericFormat
              displayType="text"
              value={amount ? amount : 0}
              thousandSeparator={true}
            />
          );
        },
      },
      {
        header: t("clients.name"),
        accessorKey: "name",
        meta: { isId: true },
      },
      {
        header: t("identity"),
        accessorKey: "identityNumber",
      },
      {
        header: t("provision"),
        accessorKey: "provision..name",
        enableSorting: true,
        meta: { isNumeric: true },
        cell: (props) => {
          const { attribute } = props.row.original;
          return (
            <NumericFormat
              displayType="text"
              value={attribute.البروفجن ? attribute.البروفجن : 0}
              thousandSeparator={true}
            />
          );
        },
      },
      {
        header: t("product"),
        accessorKey: "product..name",
        enableSorting: false,
        cell: (props) => {
          const { attribute } = props.row.original;
          return attribute.المنتج ? attribute.المنتج : "";
        },
      },

      {
        header: "",
        id: "action",
        cell: (props) => {
          return <ActionColumn row={props.row.original} />;
        },
      },
    ],
    [],
  );

  const [columns] = useState<typeof defaultColumns>(() => [...defaultColumns]);

  const { page, pageSize, sort, query, total, totalAmount } = useAppSelector(
    (state) => state.clientsList.data.tableData,
  );

  const fetchData = useFetchClients();
  useEffect(() => {
    fetchData();
  }, [page, pageSize, sort, query, filterData]);

  const tableData = useMemo(
    () => ({ page, pageSize, sort, query, total, totalAmount }),
    [page, pageSize, sort, query, total],
  );

  const onPaginationChange = (page: number) => {
    const newTableData = cloneDeep(tableData);
    newTableData.page = page;
    dispatch(setTableData(newTableData));
  };

  const onSelectChange = (value: number) => {
    const newTableData = cloneDeep(tableData);
    newTableData.pageSize = Number(value);
    newTableData.page = 1;
    dispatch(setTableData(newTableData));
  };

  const onSort = (sort: OnSortParam) => {
    const newTableData = cloneDeep(tableData);
    newTableData.sort = sort;
    dispatch(setTableData(newTableData));
  };

  const [selectedRows, setSelectedRows] = useState([]);
  const handleRowSelect = (checked: boolean, row: Customer) => {
    console.log("row", row);
    if (checked) {
      setSelectedRows((prevData) => {
        if (!prevData.includes(row.id)) {
          return [...prevData, ...[row.id]];
        }
        return prevData;
      });
    } else {
      setSelectedRows((prevData) => {
        if (prevData.includes(row.id)) {
          return prevData.filter((id) => id !== row.id);
        }
        return prevData;
      });
    }
  };

  const handleAllRowSelect = (checked: boolean, rows: Row<Customer>[]) => {
    console.log("rows", rows);
    if (checked) {
      const originalRows = rows.map((row) => row.original);
      const selectedIds: string[] = [];
      originalRows.forEach((row) => {
        selectedIds.push(row.id);
      });
      setSelectedRows(selectedIds);
    } else {
      setSelectedRows([]);
    }
  };

  useEffect(() => {
    dispatch(setSelectedRow(selectedRows));
  }, [selectedRows]);

  useEffect(() => {
    setSelectedRows([]);
    dispatch(setSelectedRow([]));
  }, [data]);

  return (
    <>
      <div className="mt-5">
        <TableOptions />
        <DataTable
          selectable
          columns={columns}
          data={data}
          skeletonAvatarColumns={[0]}
          skeletonAvatarProps={{ width: 28, height: 28 }}
          loading={loading}
          pagingData={{
            total: tableData.total as number,
            page: tableData.page as number,
            pageSize: tableData.pageSize as number,
            displayTotal: true,
          }}
          onPaginationChange={onPaginationChange}
          onSelectChange={onSelectChange}
          onSort={onSort}
          onCheckBoxChange={handleRowSelect}
          onIndeterminateCheckBoxChange={handleAllRowSelect}
          pageSizes={[100, 500, 1000]}
          footerTotalAmount={{
            status: true,
            label: t("total_amount"),
            value: tableData.totalAmount,
            place: 7,
          }}
        />
      </div>
    </>
  );
};

export default ClientsTable;
