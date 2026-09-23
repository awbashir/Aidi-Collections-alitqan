import { useEffect, useCallback, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  setTableData,
  useAppDispatch,
  useAppSelector,
  getList,
  deactivatePortfolio,
  activatePortfolio,
  Portfolio,
} from "./store";
import DataTable from "@/components/shared/DataTable";
import { Link } from "react-router-dom";
import { Button } from "@/components/ui";
import { HiPlusCircle, HiDownload } from "react-icons/hi";
import { NumericFormat } from "react-number-format";

import cloneDeep from "lodash/cloneDeep";

import ActionColumn from "./components/ActionColumn";

const TableOptions = () => {
  const { t } = useTranslation();
  return (
    <div className="mb-4 flex flex-wrap items-center gap-2">
      {/*  <Link
        download
        className="block lg:inline-block  md:mb-0 mb-4"
        to="/data/product-list.csv"
        target="_blank"
      >
        <Button block size="sm" icon={<HiDownload />}>
          {t("download")}
        </Button>
      </Link> */}
      <Link className="ms-auto" to="/settings/general/archiveReasons/new">
        <Button variant="solid" size="sm" icon={<HiPlusCircle />}>
          {t("add")}
        </Button>
      </Link>
    </div>
  );
};

const PortfoliosTable = () => {
  const { t } = useTranslation();
  const dispatch = useAppDispatch();
  const data = useAppSelector((state) => state.archiveReasonsgList.data.list);
  const loading = useAppSelector(
    (state) => state.archiveReasonsgList.data.loading
  );
  const filterData = useAppSelector(
    (state) => state.archiveReasonsgList.data.filterData
  );

  const defaultColumns: ColumnDef<User>[] = useMemo(
    () => [
      {
        header: t("name"),
        accessorKey: "name",
      },
      /*  {
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
 */
      {
        header: "",
        id: "action",
        cell: (props) => {
          return (
            <ActionColumn row={props.row.original} fetchadata={fetchData} />
          );
        },
      },
    ],
    []
  );

  const [columns] = useState<typeof defaultColumns>(() => [...defaultColumns]);

  const { page, pageSize, sort, query, total } = useAppSelector(
    (state) => state.archiveReasonsgList.data.tableData
  );

  const fetchData = useCallback(() => {
    dispatch(getList({ page, pageSize, sort, query, filterData }));
  }, [page, pageSize, sort, query, filterData, dispatch]);

  useEffect(() => {
    fetchData();
  }, [fetchData, page, pageSize, sort, filterData]);

  const tableData = useMemo(
    () => ({ page, pageSize, sort, query, total }),
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
