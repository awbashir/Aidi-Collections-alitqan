import React, { useState, useRef } from "react";
import Tooltip from "@/components/ui/Tooltip";
import { useTranslation } from "react-i18next";
import useThemeClass from "@/utils/hooks/useThemeClass";
import { deletePortfolios } from "../store";
import ToastNotification from "@/components/shared/myComponents/ToastNotification";
import ConfirmDialog from "@/components/shared/myComponents/ConfirmDialog";
import ToArchiveForm, { FormikRef } from "./form/ToArchiveForm";
import { LuArchiveRestore } from "react-icons/lu";

const MoveFromArchiveButton = ({
  row,
  fetchData,
  notificationSuccessMessage,
  notificationFailedMessage,
  notificationTitle,
  confirmDialogMessage,
  confirmDialogTitle,
  api,
}) => {
  const { t } = useTranslation();
  const { textTheme } = useThemeClass();

  const [dialogOpen, setDialogOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const onConfirmDialogOpen = () => {
    setDialogOpen(true);
  };
  const onConfirmDialogClose = () => {
    setDialogOpen(false);
  };

  const handleConfirm = async () => {
    formikRef.current?.submitForm();

    setLoading(true);
    const { accountNumber } = await api(row.id);

    if (accountNumber) {
      setLoading(false);
      fetchData();
      ToastNotification(
        t(notificationSuccessMessage),
        t(notificationTitle),
        "success"
      );
      setDialogOpen(false);
    } else {
      ToastNotification(
        t(notificationFailedMessage),
        t(notificationTitle),
        "danger"
      );
    }
  };
  const formikRef = useRef<FormikRef>(null);

  const onFormSubmit = (values: FormModel) => {
    handleConfirm();
  };

  return (
    <>
      <Tooltip title={t("archive.moveFromArchive")}>
        <span
          className={`cursor-pointer hover:text-azure-600 p-2 `}
          onClick={onConfirmDialogOpen}
        >
          <LuArchiveRestore />
        </span>
      </Tooltip>

      <ConfirmDialog
        isOpen={dialogOpen}
        type="danger"
        title={t(confirmDialogTitle)}
        cancelText={t("cancel")}
        confirmText={t("confirm")}
        confirmButtonColor="red-600"
        onClose={onConfirmDialogClose}
        onRequestClose={onConfirmDialogClose}
        onCancel={onConfirmDialogClose}
        onConfirm={handleConfirm}
        loading={loading}
      >
        <span>{t("archive.moveFromArchiveConfirm")}</span>
      </ConfirmDialog>
    </>
  );
};

export default MoveFromArchiveButton;
