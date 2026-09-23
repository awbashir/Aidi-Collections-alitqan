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
import { Button } from "@/components/ui";
import { HiPlusCircle, HiDownload, HiOutlineSearch } from "react-icons/hi";
import { NumericFormat } from "react-number-format";
import AddClientsByExcelButton from "./components/AddClientsButton";
import SepcifyUsersByExcelButton from "./components/SepcifyUsersByExcelButton";
import EnquiryByExcelButton from "./components/EnquiryByExcelButton";
import UpdateDataByExcelButton from "./components/UpdateDataByExcelButton";
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
import StatusPill from "@/components/shared/StatusPill";

const TableOptions = () => {
  const { t } = useTranslation();
  const dispatch = useAppDispatch();
  const [selectedInquiryClintByExcel, setSelectedInquiryClintByExcel] =
    useState([]);
  const pramas = useAppSelector((state) => state.clientsList2.data.filterData);

  const search = useAppSelector((state) => state.clientsList2.data.search);

  const selectedRow = useAppSelector(
    (state) => state.clientsList2.data.selectedRow
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

        customeFilterInquiryClintByExcelValue: selectedInquiryClintByExcel

        //-----------------
      })
    );
  }, [selectedInquiryClintByExcel]);

  const { permissions } = useAppSelector((state) => state.auth.user);

  return (
    <div className="flex flex-col lg:flex-row lg:flex-wrap lg:items-center mb-4 gap-y-2">
     
    </div>
  );
};

const ClientsTable = () => {
  const { t } = useTranslation();
  const dispatch = useAppDispatch();
  const data = useAppSelector((state) => state.clientsList2.data.list);
  const loading = useAppSelector((state) => state.clientsList2.data.loading);
  const filterData = useAppSelector(
    (state) => state.clientsList2.data.filterData
  );

  const defaultColumns: ColumnDef<User>[] = useMemo(
    () => [
      {
        header: t("portfolios.name"),
        accessorKey: "portfolios..name",
        enableSorting: false,
        cell: (props) => {
          const { portfolios } = props.row.original;
          return portfolios ? portfolios.name : "";
        },
      },
     
      {
        header: t("contracts.contract"),
        accessorKey: "contract..id",
        enableSorting: false,
        cell: (props) => {
          const { contract } = props.row.original;
          return contract ? contract.name : "";
        },
      },
      {
        header: t("clients.accountNumber"),
        accessorKey: "accountNumber",
         enableSorting: false,
        cell: (props) => {
          const { accountNumber } = props.row.original;
          const value = accountNumber;
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
      //amount
      {
        header: t("amountdebt"),
        accessorKey: "amount",
        meta: { isNumeric: true },
        enableSorting: false,
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
        enableSorting: false,
      },
      {
        header: t("identity"),
        accessorKey: "identityNumber",
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
        header: t("status"),
        accessorKey: "is_archive",
        enableSorting: false,
        
        cell: (props) => {
          const { is_archive } = props.row.original;
          return (
            <StatusPill tone={is_archive ? "warning" : "success"}>
              {is_archive ? t("archived") : t("active")}
            </StatusPill>
          );
        }
      },

      {
        header: "",
        id: "action",
        cell: (props) => {
          return <ActionColumn row={props.row.original} />;
        },
      },
    ],
    []
  );

  const [columns] = useState<typeof defaultColumns>(() => [...defaultColumns]);

  const { page, pageSize, sort, query, total, totalAmount } = useAppSelector(
    (state) => state.clientsList2.data.tableData
  );

  const fetchData = useFetchClients();
  useEffect(() => {
    fetchData();
  }, [page, pageSize, sort, query, filterData]);

  const tableData = useMemo(
    () => ({ page, pageSize, sort, query, total, totalAmount }),
    [page, pageSize, sort, query, total]
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
