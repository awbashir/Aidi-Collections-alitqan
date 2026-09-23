import { useEffect, useCallback, useMemo, useState } from "react";
import Avatar from "@/components/ui/Avatar";
import Badge from "@/components/ui/Badge";
import DataTable from "@/components/shared/DataTable";
import {
  getUsers,
  getBranches,
  setTableData,
  setSelectedUser,
  setDrawerOpen,
  useAppDispatch,
  useAppSelector,
  User,
  activateUser,
  deactivateUser,
} from "../store";
import useThemeClass from "@/utils/hooks/useThemeClass";
import UserEditDialog from "./UserEditDialog";
import { Link } from "react-router-dom";
import dayjs from "dayjs";
import cloneDeep from "lodash/cloneDeep";
import type { OnSortParam, ColumnDef } from "@/components/shared/DataTable";
import { useTranslation } from "react-i18next";
import { NumericFormat } from "react-number-format";
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
import useDirection from "@/utils/hooks/useDirection";
import { Button } from "@/components/ui";

const statusColor: Record<string, string> = {
  active: "bg-emerald-500",
  blocked: "bg-red-500",
};

import Tooltip from "@/components/ui/Tooltip";
import { useNavigate } from "react-router-dom";
import ConfirmDialog from "@/components/shared/ConfirmDialog";
import toast from "@/components/ui/toast";
import Notification from "@/components/ui/Notification";
import ExportByExcel from "@/components/shared/myComponents/exportUsers";

const ActionColumn = ({ row, fetchadata }: { row: User; fetchadata: void }) => {
  const { t } = useTranslation();
  const [direction, updateDirection] = useDirection();
  const { textTheme } = useThemeClass();
  const dispatch = useAppDispatch();
  const navigate = useNavigate();
  const { permissions } = useAppSelector((state) => state.auth.user); // ← جديد

  const onEdit = () => {
    dispatch(setDrawerOpen());
    dispatch(setSelectedUser(row));
  };

  const onDelete = () => {
    console.log("Delete", row.id);
  };

  const onDetail = useCallback(() => {
    navigate(`/users/${row.id}`);
  }, [navigate, row]);

  const onTogleActive = () => {
    console.log("Togle Active", row.id);
  };

  const onShowClients = () => {
    console.log("Show Clients", row.id);
  };

  return (
    <div className="flex items-center justify-end gap-1 text-base">
      {permissions.users_details && (
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
      )}

      {permissions.users_edit && (
        <Tooltip title={t("edit")}>
          <Link
            className={`cursor-pointer p-2 hover:text-azure-600`}
            to={`/users/edit?id=${row.id}`}
          >
            <span>
              <HiOutlinePencil />
            </span>
          </Link>
        </Tooltip>
      )}

      {/*   <Tooltip title={t("clients.view")}>
        <span
          className={`cursor-pointer p-2 hover:text-azure-600`}
          onClick={onShowClients}
        >
          <HiOutlineUserGroup />
        </span>
      </Tooltip> */}

      {permissions.users_delete && (
        <DeleteProductButton
          onDelete={onDelete}
          row={row}
          fetchData={fetchadata}
        />
      )}
    </div>
  );
};

const NameColumn = ({ row }: { row: User }) => {
  const { textTheme } = useThemeClass();

  return (
    <div className="flex items-center">
      <Avatar size={28} shape="circle" src={row.img} />
      <Link
        className={`hover:text-azure-600 ml-2 rtl:mr-2 font-semibold`}
        to={`/app/crm/user-details?id=${row.id}`}
      >
        {row.name}
      </Link>
    </div>
  );
};

const TableOptions = () => {
  const { t } = useTranslation();
  const pramas = useAppSelector((state) => state.crmUsers.data.filterData);
  const { permissions } = useAppSelector((state) => state.auth.user); // ← جديد

  return (
    <div className="mb-4 flex flex-wrap items-center gap-2">
      <div className="ms-auto flex flex-wrap items-center gap-2">
        <ExportByExcel
          pramas={pramas}
          notificationFailedMessage={t("users.failExport")}
          notificationTitle={t("users.exportTitle")}
          notificationSuccessMessage={t("users.successExport")}
        />
        {permissions.users_add && (
          <Link to="/users/new">
            <Button variant="solid" size="sm" icon={<HiPlusCircle />}>
              {t("add")}
            </Button>
          </Link>
        )}
      </div>
    </div>
  );
};

const DeleteProductButton = ({
  row,
  fetchData,
}: {
  row: User;
  fetchData: void;
}) => {
  const [dialogOpen, setDialogOpen] = useState(false);
  const { t } = useTranslation();
  const dispatch = useAppDispatch();
  const navigate = useNavigate();

  const onConfirmDialogOpen = () => {
    setDialogOpen(true);
  };

  const onConfirmDialogClose = () => {
    setDialogOpen(false);
  };
  const popNotification = (keyword: string) => {
    toast.push(
      <Notification title={t("users.activate")} type="success" duration={2500}>
        {keyword}
      </Notification>,
      {
        placement: "top-center",
      },
    );
    navigate("/users");
  };

  const handleConfirm = async () => {
    const success = row.is_active
      ? await deactivateUser(row)
      : await activateUser(row);
    setDialogOpen(!success);
    if (success) {
      fetchData();
      popNotification(
        row.is_active
          ? t("users.activateSuccess")
          : t("users.deactivateSuccess"),
      );
    }
  };

  return (
    <>
      <Tooltip title={t(row.is_active ? "deactivate" : "activate")}>
        <span
          className={`cursor-pointer p-2 ${
            row.is_active ? "text-red-500" : "text-green-500"
          }`}
          onClick={onConfirmDialogOpen}
        >
          {row.is_active ? <HiOutlineX /> : <HiOutlineCheck />}
        </span>
      </Tooltip>
      <ConfirmDialog
        isOpen={dialogOpen}
        type="danger"
        title={row.is_active ? t("users.deactivate") : t("users.activate")}
        cancelText={t("cancel")}
        confirmText={t("confirm")}
        confirmButtonColor="red-600"
        onClose={onConfirmDialogClose}
        onRequestClose={onConfirmDialogClose}
        onCancel={onConfirmDialogClose}
        onConfirm={handleConfirm}
      >
        <p>
          {row.is_active
            ? t("users.confirmDeactivate")
            : t("users.confirmActivate")}
        </p>
      </ConfirmDialog>
    </>
  );
};

const Users = () => {
  const { t } = useTranslation();
  const dispatch = useAppDispatch();
  const data = useAppSelector((state) => state.crmUsers.data.userList);
  const loading = useAppSelector((state) => state.crmUsers.data.loading);
  const filterData = useAppSelector((state) => state.crmUsers.data.filterData);

  const defaultColumns: ColumnDef<User>[] = useMemo(
    () => [
      {
        header: t("name"),
        accessorKey: "name",
        cell: (props) => {
          const row = props.row.original;
          return <NameColumn row={row} />;
        },
      },
      {
        header: t("users.name"),
        accessorKey: "user_name",
      },
      {
        header: t("email"),
        accessorKey: "email",
        cell: (props) => {
          const value = props.row.original?.email;
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
        header: t("permissions"),
        accessorKey: "role..name",
        cell: (props) => {
          const row = props.row.original;
          return row.role.name;
        },
      },

      {
        header: t("nationality"),
        accessorKey: "nationality..name",
        cell: (props) => {
          const row = props.row.original;
          return row.nationality.name;
        },
      },

      {
        header: t("identity"),
        accessorKey: "identityNumber",
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
        header: t("branch"),
        accessorKey: "branch..name",
        cell: (props) => {
          const row = props.row.original;
          return row.branch.name;
        },
      },
      {
        header: t("supervisor"),
        accessorKey: "supervisor",
        size: "200",
        cell: (props) => {
          const row = props.row.original;
          return row.supervisoruser
            .map((supervisor) => supervisor.supervisor.user.name)
            .join(", ");
        },
        sort: false,
      },
      {
        header: t("clients.count"),
        accessorKey: "count_clints",
        meta: { isNumeric: true },
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
        cell: (props) => (
          <ActionColumn row={props.row.original} fetchadata={fetchData} />
        ),
      },
    ],
    [],
  );

  const [columns] = useState<typeof defaultColumns>(() => [...defaultColumns]);

  const { page, pageSize, sort, query, total } = useAppSelector(
    (state) => state.crmUsers.data.tableData,
  );

  const fetchData = useCallback(() => {
    dispatch(getUsers({ page, pageSize, sort, query, filterData }));
  }, [page, pageSize, sort, query, filterData, dispatch]);

  const fetchBranches = useCallback(() => {
    dispatch(getBranches());
  }, [dispatch]);

  useEffect(() => {
    fetchData();
  }, [fetchData, page, pageSize, sort, filterData]);

  useEffect(() => {
    fetchBranches();
  }, [fetchBranches]);

  const tableData = useMemo(
    () => ({ page, pageSize, sort, query, total }),
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

  return (
    <>
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
      />
      <UserEditDialog />
    </>
  );
};

export default Users;
