import { useEffect, useCallback, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  setTableData,
  useAppDispatch,
  useAppSelector,
  getPortfolios,
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
import ExportByExcel from "@/components/shared/myComponents/exportPortfolios";
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

  const pramas = useAppSelector(
    (state) => state.inactiveClientsList.data.filterData
  );

  const search = useAppSelector(
    (state) => state.inactiveClientsList.data.search
  );

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
  const data = useAppSelector((state) => state.inactiveClientsList.data.list);
  const loading = useAppSelector(
    (state) => state.inactiveClientsList.data.loading
  );
  const { page, pageSize, sort, query, total } = useAppSelector(
    (state) => state.inactiveClientsList.data.tableData
  );
  const filterData = useAppSelector(
    (state) => state.inactiveClientsList.data.filterData
  );
  const tableData = useMemo(
    () => ({ page, pageSize, sort, query, total }),
    [page, pageSize, sort, query, total]
  );

  const defaultColumns: ColumnDef<User>[] = useMemo(
    () => [
      {
        header: t("portfolios.name"),
        accessorKey: "name",
      },
      {
        header: t("contracts.name"),
        accessorKey: "contracts..id",

        cell: (props) => {
          const { contracts } = props.row.original;
          return contracts ? contracts.name : "";
        },
      },

      {
        header: t("portfolios.created_at"),
        accessorKey: "create_at",
        cell: (props) => {
          const { create_at } = props.row.original;
          const date = new Date(create_at);
          const year = date.getFullYear();
          const month = date.getMonth() + 1;
          const dt = date.getDate();

          return `${year}-${month}-${dt}`;
        },
      },
      {
        header: t("portfolios.created_atCountDays"),
        accessorKey: "create_at2",
        meta: { isNumeric: true },
        cell: (props) => {
          const { create_at } = props.row.original;
          const date = new Date(create_at);
          const now = new Date();
          const diff = now.getTime() - date.getTime();
          const days = Math.floor(diff / (1000 * 60 * 60 * 24));
          return days;
        },
      },

      {
        header: t("clients.count"),
        accessorKey: "countClints",
        meta: { isNumeric: true },

        cell: (props) => {
          const { countClints } = props.row.original;
          return (
            <NumericFormat
              displayType="text"
              value={countClints ? countClints : 0}
              thousandSeparator={true}
            />
          );
        },
      },
      {
        header: t("amountdebt"),
        accessorKey: "countClintsAmount",
        meta: { isNumeric: true },
        cell: (props) => {
          const { countClintsAmount } = props.row.original;
          return (
            <NumericFormat
              displayType="text"
              value={countClintsAmount ? countClintsAmount.toFixed(2) : 0}
              thousandSeparator={true}
            />
          );
        },
      },

      {
        header: t("amountCollected"),
        accessorKey: "payments",
        meta: { isNumeric: true },
        cell: (props) => {
          const { payments } = props.row.original;
          return (
            <NumericFormat
              displayType="text"
              value={payments ? payments : 0}
              thousandSeparator={true}
            />
          );
        },
      },
      {
        header: t("remaining"),
        accessorKey: "paymentsـremaining",
        meta: { isNumeric: true },
        cell: (props) => {
          const { payments, countClintsAmount } = props.row.original;
          return (
            <NumericFormat
              displayType="text"
              value={
                countClintsAmount && payments
                  ? (countClintsAmount - payments).toFixed(2)
                  : 0
              }
              thousandSeparator={true}
            />
          );
        },
      },

      /*    {
        header: t("users.name"),
        accessorKey: "name",
        cell: (props) => {
          const { name } = props.row.original?.user;
          return name;
        },
      },
      {
        header: t("amount"),
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

      {
        header: t("payments.date"),
        accessorKey: "payment_date",
        cell: (props) => {
          const { payment_date } = props.row.original;
          const date = new Date(payment_date);
          const year = date.getFullYear();
          const month = date.getMonth() + 1;
          const dt = date.getDate();

          return `${year}-${month}-${dt}`;
        },
      },

      {
        header: t("payments.addDate"),
        accessorKey: "create_at",
        cell: (props) => {
          const { create_at } = props.row.original;
          const date = new Date(create_at);
          const year = date.getFullYear();
          const month = date.getMonth() + 1;
          const dt = date.getDate();

          return `${year}-${month}-${dt}`;
        },
      },

      {
        header: t("clients.accountNumber"),
        accessorKey: "clint..accountNumber",
        cell: (props) => {
          const { accountNumber } = props.row.original?.clint;
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
        header: t("contracts.name"),
        accessorKey: "contracts..id",

        cell: (props) => {
          const { name } = props.row.original?.clint?.portfolios?.contract;
          return name;
        },
      },
      {
        header: t("clients.name"),
        accessorKey: "clint..name",
        cell: (props) => {
          const { name } = props.row.original?.clint;
          return name;
        },
      },

      {
        header: t("clients.identityNumber"),
        accessorKey: "clint..identityNumber",
        cell: (props) => {
          const { identityNumber } = props.row.original?.clint;
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
        accessorKey: "clint..amount",
        meta: { isNumeric: true },
        cell: (props) => {
          const { amount } = props.row.original?.clint;
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

      {
        header: t("payments.method"),
        accessorKey: "collection_method..name",
        cell: (props) => {
          const { name } = props.row.original?.collection_method;
          return name;
        },
      },

      {
        header: t("payments.type"),
        accessorKey: "collection_type..name",
        cell: (props) => {
          const { name } = props.row.original?.collection_type;
          return name;
        },
      },

      {
        header: t("payments.approval_at"),
        accessorKey: "approval_at",
        cell: (props) => {
          const { approval_at } = props.row.original;
          if (!approval_at) return "";
          const date = new Date(approval_at);
          const year = date.getFullYear();
          const month = date.getMonth() + 1;
          const dt = date.getDate();

          return `${year}-${month}-${dt}`;
        },
      },

      {
        header: t("payments.approval_by"),
        accessorKey: "approval_by",
        cell: (props) => {
          const { approval_by } = props.row.original;
          return approval_by?.name;
        },
      },

      {
        header: t("payments.approval_comment"),
        accessorKey: "approval_comment",
        cell: (props) => {
          const { approval_comment } = props.row.original;
          return approval_comment;
        },
      },

      {
        header: "",
        id: "action",
        cell: (props) => {
          return <ActionColumn row={props.row.original} />;
        },
      }, */
    ],
    []
  );

  const [columns] = useState<typeof defaultColumns>(() => [...defaultColumns]);

  const fetchData = useCallback(() => {
    dispatch(getPortfolios({ page, pageSize, sort, query, filterData }));
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
      <div className="mt-5">
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
        />
      </div>
    </>
  );
};

export default PortfoliosTable;
