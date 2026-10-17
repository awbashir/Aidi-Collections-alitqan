import { useEffect, useCallback, useMemo, useState } from "react";
import useListScrollRestore from "@/utils/hooks/useListScrollRestore";
import { useTranslation } from "react-i18next";
import {
  setTableData,
  useAppDispatch,
  useAppSelector,
  getClintNotHavePayments,
  deactivatePortfolio,
  activatePortfolio,
  Portfolio,
  setFilterData,
  setSearchOpen,
  setSearchClose,
} from "./store";
import { apiGetBranches } from "@/services/CollectionsService";
import DataTable from "@/components/shared/DataTable";
import { Link } from "react-router-dom";
import { Button } from "@/components/ui";
import { HiPlusCircle, HiDownload } from "react-icons/hi";
import { NumericFormat } from "react-number-format";
import ExportExcel from "@/components/shared/myComponents/export";
import ExportByExcel from "@/components/shared/myComponents/exportNneglect";
import InquiryClintByExcel from "@/components/shared/myComponents/modelForm";

import cloneDeep from "lodash/cloneDeep";

import ActionColumn from "./components/ActionColumn";
import { use } from "i18next";
import MoveFromArchiveByExcel from "@/components/shared/myComponents/modelFormMoveFromArchiveByExcel";
import { HiOutlineSearch } from "react-icons/hi";
import MyFilter from "@/components/shared/myComponents/filter";
import FilterDate from "@/components/shared/myComponents/filterDate";

function formatDate(date) {
  let d = new Date(date),
    month = "" + (d.getMonth() + 1),
    day = "" + d.getDate(),
    year = d.getFullYear();

  if (month.length < 2) month = "0" + month;
  if (day.length < 2) day = "0" + day;

  return [year, month, day].join("-");
}

const TableOptions = () => {
  const { t } = useTranslation();
  const dispatch = useAppDispatch();

  const pramas = useAppSelector((state) => state.neglectList.data.filterData);

  const search = useAppSelector((state) => state.neglectList.data.search);

  const [branches, setBranches] = useState([]);
  const [selectedBranches_, setSelectedBranches_] = useState([]);
  const current = new Date(); // get current date
  const firstDayOfMonth = new Date(
    current.getFullYear(),
    current.getMonth(),
    1
  );
  const lastDayOfMonth = new Date(
    current.getFullYear(),
    current.getMonth() + 1,
    0
  );
  const [selectedMoveDate, setSelectedMoveDate] = useState([
    formatDate(firstDayOfMonth),
    formatDate(lastDayOfMonth),
  ]);
  const fetchBranches = async () => {
    const response = await apiGetBranches();
    setBranches(response.data.branches);
  };

  useEffect(() => {
    fetchBranches();
  }, []);

  const onclick = () => {
    search ? dispatch(setSearchClose()) : dispatch(setSearchOpen());
  };

  useEffect(() => {
    if (
      JSON.stringify(pramas?.customeFilterBranchesValue2) !==
        JSON.stringify(selectedBranches_) ||
      JSON.stringify(pramas?.customeFilterDateValue) !==
        JSON.stringify(selectedMoveDate)
    ) {
      dispatch(
        setFilterData({
          ...pramas,

          customeFilterBranchesValue2: selectedBranches_?.map(
            (branches) => branches.id
          ),

          customeFilterDateValue: selectedMoveDate,
          //-----------------
        })
      );
    }
  }, [selectedMoveDate, selectedBranches_, dispatch]);
  return (
    <>
      <div className="mb-2">
        <div className="grid grid-cols-3 gap-4 pb-2">
          {/*   <MyFilter
            options={branches}
            filter={setSelectedBranches_}
            labelField={{
              plural: "الفروع",
              singular: "الفرع",
              title: "الفرع",
            }}
            isMulti={false}
          /> */}
          {/*  <FilterDate
            filter={setSelectedMoveDate}
            label="تاريخ السداد"
            defulltFrom={firstDayOfMonth}
            defulltTo={lastDayOfMonth}
          /> */}
        </div>
      </div>

      <div className="mb-4 flex flex-wrap items-center gap-2">
        <Button
          icon={<HiOutlineSearch />}
          variant="default"
          size="sm"
          onClick={onclick}
        >
          بحث
        </Button>

        <div className="ms-auto flex flex-wrap items-center gap-2">
          <ExportByExcel
            pramas={pramas}
            notificationFailedMessage={t("archive.failExport")}
            notificationTitle={t("archive.exportTitle")}
            notificationSuccessMessage={t("archive.successExport")}
          />
        </div>
      </div>
    </>
  );
};

const PortfoliosTable = () => {
  const { t } = useTranslation();
  const dispatch = useAppDispatch();
  const data = useAppSelector((state) => state.neglectList.data.list);
  const loading = useAppSelector((state) => state.neglectList.data.loading);
  /* Rows open the case in this tab; the list keeps its scroll for the way
   * back. */
  const { rootRef: scrollRootRef } = useListScrollRestore({ loading });
  const { page, pageSize, sort, query, total, totalAmount } = useAppSelector(
    (state) => state.neglectList.data.tableData
  );
  const filterData = useAppSelector(
    (state) => state.neglectList.data.filterData
  );
  const tableData = useMemo(
    () => ({ page, pageSize, sort, query, total, totalAmount }),
    [page, pageSize, sort, query, total]
  );

  const defaultColumns: ColumnDef<User>[] = useMemo(
    () => [
      {
        header: t("portfolios.name"),
        accessorKey: "portfolios..name",
        cell: (props) => {
          const { name } = props.row.original?.portfolios;
          return name;
        },
      },

      {
        header: t("contracts.name"),
        accessorKey: "contract..name",

        cell: (props) => {
          const { name } = props.row.original?.contract;
          return name;
        },
      },

      {
        header: t("users.name"),
        accessorKey: "user..name",
        cell: (props) => {
          if (!props.row.original?.user) return "";
          const { name } = props.row.original?.user;

          return name;
        },
      },

      {
        header: t("comments.lastCommentDate"),
        accessorKey: "comment..create_at",
        cell: (props) => {
          if (!props.row.original?.comment) return "";
          const { create_at } = props.row.original?.comment;
          if (!create_at) return "";
          const date = new Date(create_at).toLocaleString("en-sa");

          return date;
        },
      },
      {
        header: t("comments.lastCommentDateSince"),
        accessorKey: "create_at",
        meta: { isNumeric: true },
        cell: (props) => {
          const { create_at } = props.row.original?.comment
            ? props.row.original?.comment
            : "";
          console.log("create_at", create_at);
          if (!create_at) return 0;

          const date = new Date(create_at);
          const now = new Date();
          const diff = now.getTime() - date.getTime();
          const days = Math.floor(diff / (1000 * 60 * 60 * 24));

          return days ? days : 0;
        },
      },

      {
        header: t("comments.comment"),
        accessorKey: "comment.comment",
        cell: (props) => {
          if (!props.row.original?.comment) return "";
          const { comment } = props.row.original?.comment;
          return comment;
        },
      },

      {
        header: t("comments.status"),
        accessorKey: "comment_status..name",
        cell: (props) => {
          if (!props.row.original?.comment_status) return "";
          const { name } = props.row.original?.comment_status;
          return name;
        },
      },
      {
        header: t("p8.reports.subStatus"),
        accessorKey: "comment_sub_status..name",
        cell: ({
          row,
        }: {
          row: { original?: { comment_sub_status?: { name?: string } | null } };
        }) => row.original?.comment_sub_status?.name || "",
      },

      {
        header: t("clients.accountNumber"),
        accessorKey: "accountNumber",
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

      {
        header: t("clients.name"),
        accessorKey: "name",
        cell: (props) => {
          const { name } = props.row.original;
          return name;
        },
      },

      {
        header: t("clients.identityNumber"),
        accessorKey: "identityNumber",
        cell: (props) => {
          const { identityNumber } = props.row.original;
          const value = identityNumber;
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
              decimalScale={2}
              fixedDecimalScale={true}
            />
          );
        },
      },
    ],
    []
  );

  const [columns] = useState<typeof defaultColumns>(() => [...defaultColumns]);

  const fetchData = useCallback(() => {
    dispatch(
      getClintNotHavePayments({ page, pageSize, sort, query, filterData })
    );
  }, [page, pageSize, sort, query, filterData, dispatch]);

  useEffect(() => {
    fetchData();
  }, [fetchData, page, pageSize, sort, filterData]);

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

  return (
    <>
      <div className="mt-5" ref={scrollRootRef}>
        <TableOptions />
        <DataTable
          columns={columns}
          data={data}
          skeletonAvatarColumns={[0]}
          skeletonAvatarProps={{ width: 28, height: 28 }}
          loading={loading}
          pagingData={{
            total: tableData.total as number,
            page: tableData.page as number,
            pageSize: tableData.pageSize as number,
          }}
          onPaginationChange={onPaginationChange}
          onSelectChange={onSelectChange}
          onSort={onSort}
          pageSizes={[100, 500, 1000]}
          clickAbility={true}
          footerTotalAmount={{
            status: true,
            label: t("total_amount"),
            value: tableData.totalAmount,
            place: 8,
          }}
        />
      </div>
    </>
  );
};

export default PortfoliosTable;
