import React, { useCallback } from "react";
import useDirection from "@/utils/hooks/useDirection";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import useThemeClass from "@/utils/hooks/useThemeClass";
import {
  useAppDispatch,
  deletePortfolios,
  putPortfoliolsToArchive,
  putRejectPayment,
} from "../store";
import Tooltip from "@/components/ui/Tooltip";
import { Link } from "react-router-dom";
import {
  HiOutlinePencil,
  HiOutlineUserGroup,
  HiArrowSmLeft,
  HiOutlineTrash,
  HiOutlineArchive,
} from "react-icons/hi";
import ToggleStatusButton from "./ToggleStatusButton";
import DeleteButton from "./DeleteButton";
import MoveFromArchiveButton from "./MoveFromArchiveButton";
import EditPaymentButton from "./EditPaymentButton";
import AprrovePaymentButton from "./AprrovePaymentButton";
import { useAppSelector as useRootSelector } from "@/store";

import { LuArchiveRestore } from "react-icons/lu";

const ActionColumn = ({ row }: { row: Portfolio }) => {
  const { permissions } = useRootSelector((state) => state.auth.user);
  const canApprove = Boolean((permissions as Record<string, boolean> | undefined)?.collections_users);
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
      {/* <Tooltip title={t("details")}>
        <Link
          className={`cursor-pointer p-2 hover:text-azure-600 font-semibold`}
          to={`/clients/details/details?id=${row.id}`}
        >
          <span>
            {direction === "rtl" ? <HiArrowSmLeft /> : <HiArrowSmRight />}
          </span>
        </Link>
      </Tooltip>

      <EditPaymentButton
        row={row}
        notificationTitle={"payments.edit"}
        notificationFailedMessage={"payments.editFail"}
        notificationSuccessMessage={"payments.editSuccess"}
        confirmDialogTitle={"payments.edit"}
        confirmDialogMessage={"portfolios.confirmArchive"}
        api={putPayment}
      /> */}

      {/* Approve/reject is for supervisors only (collections_users); the server
          refuses it for everyone else. */}
      {canApprove && (
      <AprrovePaymentButton
        row={row}
        notificationTitle={"payments.edit"}
        notificationFailedMessage={"payments.editFail"}
        notificationSuccessMessage={"payments.editSuccess"}
        confirmDialogTitle={"payments.confirmReject"}
        confirmDialogMessage={"portfolios.confirmArchive"}
        api={putRejectPayment}
      />
      )}

      {/*  <DeleteButton
        row={row}
        fetchData={fetchadata}
        notificationTitle={"portfolios.delete"}
        notificationFailedMessage={"portfolios.deleteFail"}
        notificationSuccessMessage={"portfolios.deleteSuccess"}
        confirmDialogTitle={"portfolios.delete"}
        confirmDialogMessage={"portfolios.confirmDelete"}
        api={deletePortfolios}
      />
 */}
      {/* 
      <Tooltip title={t("clients.view")}>
        <span className={`cursor-pointer p-2 hover:text-azure-600`}>
          <HiOutlineUserGroup />
        </span>
      </Tooltip> */}

      {/* <ToggleStatusButton row={row} fetchData={fetchadata} /> */}
    </div>
  );
};

export default ActionColumn;
