import React, { useState } from "react";
import { useTranslation } from "react-i18next";
import { HiOutlineCheck, HiOutlineX } from "react-icons/hi";
import {
  useAppDispatch,
  deactivateContract,
  activateContract,
  Contracts,
} from "../store";
import Tooltip from "@/components/ui/Tooltip";
import ConfirmDialog from "@/components/shared/ConfirmDialog";
import PopNotification from "./popNotification";

const ToggleStatusButton = ({
  row,
  fetchData,
}: {
  row: Contracts;
  fetchData: void;
}) => {
  const [dialogOpen, setDialogOpen] = useState(false);
  const { t } = useTranslation();
  const dispatch = useAppDispatch();

  const onConfirmDialogOpen = () => {
    setDialogOpen(true);
  };

  const onConfirmDialogClose = () => {
    setDialogOpen(false);
  };

  const handleConfirm = async () => {
    const success = row.is_active
      ? await deactivateContract(row)
      : await activateContract(row);
    setDialogOpen(!success);
    if (success) {
      fetchData();
      PopNotification(
        row.is_active
          ? t("contracts.deactviatedSuccess")
          : t("contracts.actviatedSuccess")
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
        title={
          row.is_active ? t("contracts.deactivate") : t("contracts.activate")
        }
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
            ? t("contracts.confirmDeactivate")
            : t("contracts.confirmActivate")}
        </p>
      </ConfirmDialog>
    </>
  );
};

export default ToggleStatusButton;
