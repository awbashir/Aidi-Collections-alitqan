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
      <Link className="ms-auto" to="/branch/add">
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
  const data = useAppSelector((state) => state.branchList.data.list);
  const loading = useAppSelector((state) => state.branchList.data.loading);
  const filterData = useAppSelector(
    (state) => state.branchList.data.filterData
  );

  const defaultColumns: ColumnDef<User>[] = useMemo(
    () => [
      {
        header: t("branches.name"),
        accessorKey: "name",
        enableSorting: false,
      },

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
    (state) => state.branchList.data.tableData
  );

  const fetchData = useCallback(() => {
    dispatch(getPortfolios({ page, pageSize, sort, query, filterData }));
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
