import { useEffect, useCallback, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  setTableData,
  useAppDispatch,
  useAppSelector,
  getContracts,
  deactivateContract,
  activateContract,
  Contract,
} from "./store";
import DataTable from "@/components/shared/DataTable";
import { Link } from "react-router-dom";
import { Button } from "@/components/ui";
import {
  HiOutlinePencil,
  HiOutlineTrash,
  HiArrowSmRight,
  HiArrowSmLeft,
  HiOutlineCheck,
  HiOutlineX,
  HiOutlineUserGroup,
  HiViewBoards,
  HiOutlineUpload,
  HiPlusCircle,
  HiDownload,
} from "react-icons/hi";
import { NumericFormat } from "react-number-format";
import useDirection from "@/utils/hooks/useDirection";
import useThemeClass from "@/utils/hooks/useThemeClass";
import { useNavigate } from "react-router-dom";
import Tooltip from "@/components/ui/Tooltip";

import ConfirmDialog from "@/components/shared/ConfirmDialog";
import cloneDeep from "lodash/cloneDeep";

import PopNotification from "./components/PopNotification";

import ToggleStatusButton from "./components/ToggleStatusButton";

const ActionColumn = ({
  row,
  fetchadata,
}: {
  row: Contract;
  fetchadata: void;
}) => {
  const { t } = useTranslation();
  const [direction, updateDirection] = useDirection();
  const { textTheme } = useThemeClass();
  const dispatch = useAppDispatch();
  const navigate = useNavigate();

  const onEdit = () => {
    dispatch(setDrawerOpen());
    dispatch(setSelectedUser(row));
  };

  const onDetail = useCallback(() => {
    navigate(`/users/${row.id}`);
  }, [navigate, row]);

  return (
    <div className="flex items-center justify-end gap-1 text-base">
      <Tooltip title={t("details")}>
        <Link
          className={`cursor-pointer p-2 hover:text-azure-600 font-semibold`}
          to={`/users/details?id=${row.id}`}
        >
          <span>
            {direction === "rtl" ? <HiArrowSmLeft /> : <HiArrowSmRight />}
          </span>
        </Link>
      </Tooltip>

      <Tooltip title={t("edit")}>
        <Link
          className={`cursor-pointer p-2 hover:text-azure-600`}
          to={`/contracts/edit?id=${row.id}`}
        >
          <span>
            <HiOutlinePencil />
          </span>
        </Link>
      </Tooltip>

      <Tooltip title={t("clients.view")}>
        <span className={`cursor-pointer p-2 hover:text-azure-600`}>
          <HiOutlineUserGroup />
        </span>
      </Tooltip>

      <ToggleStatusButton row={row} fetchData={fetchadata} />
    </div>
  );
};
const TableOptions = () => {
  const { t } = useTranslation();
  return (
    <div className="mb-4 flex flex-wrap items-center gap-2">
      <div className="ms-auto flex flex-wrap items-center gap-2">
        <Link download to="/data/product-list.csv" target="_blank">
          <Button variant="default" size="sm" icon={<HiDownload />}>
            {t("download")}
          </Button>
        </Link>
        <Link to="/contracts/new">
          <Button variant="solid" size="sm" icon={<HiPlusCircle />}>
            {t("add")}
          </Button>
        </Link>
      </div>
    </div>
  );
};

const ContractsTable = () => {
  const { t } = useTranslation();
  const dispatch = useAppDispatch();
  const data = useAppSelector((state) => state.contractsList.data.list);
  const loading = useAppSelector((state) => state.contractsList.data.loading);
  const filterData = useAppSelector(
    (state) => state.contractsList.data.filterData
  );

  const defaultColumns: ColumnDef<User>[] = useMemo(
    () => [
      {
        header: t("name"),
        accessorKey: "name",
      },

      {
        header: t("clients.count"),
        accessorKey: "count_clints",
        meta: { isNumeric: true },

        cell: (props) => {
          const { count_clints } = props.row.original;
          return (
            <NumericFormat
              displayType="text"
              value={count_clints ? count_clints : 0}
              thousandSeparator={true}
            />
          );
        },
      },
      {
        header: t("total_amount"),
        accessorKey: "total_amount",
        meta: { isNumeric: true },
        cell: (props) => {
          const { total_amount } = props.row.original;
          return (
            <NumericFormat
              displayType="text"
              value={total_amount ? total_amount.toFixed(2) : 0}
              thousandSeparator={true}
            />
          );
        },
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
    (state) => state.contractsList.data.tableData
  );

  const fetchData = useCallback(() => {
    dispatch(getContracts({ page, pageSize, sort, query, filterData }));
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

export default ContractsTable;
