import React, { useCallback } from "react";
import useDirection from "@/utils/hooks/useDirection";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import useThemeClass from "@/utils/hooks/useThemeClass";
import { useAppDispatch, deleteClients, putClientlsToArchive } from "../store";
import Tooltip from "@/components/ui/Tooltip";
import { Link } from "react-router-dom";
import {
  HiOutlinePencil,
  HiOutlineUserGroup,
  HiArrowSmLeft,
  HiArrowSmRight,
  HiOutlineTrash,
  HiOutlineArchive,
  HiLockClosed,
  HiLockOpen,
} from "react-icons/hi";
import ToggleStatusButton from "./ToggleStatusButton";
import DeleteButton from "./DeleteButton";
import MoveArchiveButton from "./MoveArchiveButton";
import MoveToArchiveButton from "./MoveToArchiveButton";
import SepcifyUsersOneButton from "./SepcifyUsersButton";
import LockUnlockButton from "./LockUnlockButton";
import { useAppSelector } from "@/store";

const ActionColumn = ({
  row,
  fetchadata,
  onOpenDetail,
}: {
  row: Client;
  fetchadata: void;
  /** Called when the case detail link is opened (records the selected case). */
  onOpenDetail?: (id: string) => void;
}) => {
  const { t } = useTranslation();
  const [direction, updateDirection] = useDirection();
  const { textTheme } = useThemeClass();
  const dispatch = useAppDispatch();
  const navigate = useNavigate();
  const { permissions } = useAppSelector((state) => state.auth.user);

  const onEdit = () => {
    dispatch(setDrawerOpen());
    dispatch(setSelectedUser(row));
  };

  const onDetail = useCallback(() => {
    navigate(`/users/${row.id}`);
  }, [navigate, row]);

  return (
    <div className="flex justify-end items-center text-[16px] text-gray-600 dark:text-night-muted">
      <Tooltip title={t("details")}>
        <Link
          target="_blank"
          className={`cursor-pointer p-2 hover:text-azure-600 font-semibold`}
          to={`/clients/details/details?id=${row.id}`}
          onClick={() => onOpenDetail?.(row.id)}
        >
          <span>
            {direction === "rtl" ? <HiArrowSmLeft /> : <HiArrowSmRight />}
          </span>
        </Link>
      </Tooltip>

    {/* Lock status icon - visible for all users */}
      <Tooltip title={row.is_locked ? t("clients.locked") : t("clients.unlocked")}>
        <span className={`p-2 ${row.is_locked ? 'text-danger' : 'text-success'}`}>
          {row.is_locked ? <HiLockClosed /> : <HiLockOpen />}
        </span>
      </Tooltip>

      {/* Lock/Unlock action button - only for users with permission */}
      {permissions.clints_lock && (
        <LockUnlockButton
          row={row}
          notificationTitle={row.is_locked ? "clients.unlock" : "clients.lock"}
          notificationFailedMessage={row.is_locked ? "clients.unlockFail" : "clients.lockFail"}
          notificationSuccessMessage={row.is_locked ? "clients.unlockSuccess" : "clients.lockSuccess"}
          confirmDialogTitle={row.is_locked ? "clients.unlock" : "clients.lock"}
          confirmDialogMessage={row.is_locked ? "clients.confirmUnlock" : "clients.confirmLock"}
        />
      )}
      {permissions.clints_move_archive && (
        <MoveToArchiveButton
          notificationFailedMessage={t("uploadFail")}
          notificationTitle={t("uploadTitle")}
          notificationSuccessMessage={t("uploadSuccess")}
          id={row.id}
        />
      )}
      {permissions.clints_sepcifyUser && (
        <SepcifyUsersOneButton
          notificationFailedMessage={t("uploadFail")}
          notificationTitle={t("uploadTitle")}
          notificationSuccessMessage={t("uploadSuccess")}
          id={row.id}
        />
      )}
      {permissions.clints_delete && (
        <DeleteButton
          row={row}
          notificationTitle={"clients.delete"}
          notificationFailedMessage={"clients.deleteFail"}
          notificationSuccessMessage={"clients.deleteSuccess"}
          confirmDialogTitle={"clients.delete"}
          confirmDialogMessage={"clients.confirmDelete"}
          api={deleteClients}
        />
      )}

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
