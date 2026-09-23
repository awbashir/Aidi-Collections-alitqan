import React, { useState, useRef } from "react";
import Tooltip from "@/components/ui/Tooltip";
import { useTranslation } from "react-i18next";
import useThemeClass from "@/utils/hooks/useThemeClass";
import { HiOutlineDelinquency } from "react-icons/hi";
import { deletePortfolios } from "../store";
import ToastNotification from "@/components/shared/myComponents/ToastNotification";
import ConfirmDialog from "@/components/shared/myComponents/ConfirmDialog";
import ToDelinquencyForm, { FormikRef } from "./form/ToDelinquencyForm";

const MoveDelinquencyButton = ({
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
    const { status } = await api(row.id);

    if (status) {
      setLoading(false);
      fetchData();
      ToastNotification(
        t(notificationSuccessMessage),
        t(notificationTitle),
        "success"
      );
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
      <Tooltip title={t("moveDelinquency")}>
        <span
          className={`cursor-pointer hover:text-azure-600 p-2 `}
          onClick={onConfirmDialogOpen}
        >
          <HiOutlineDelinquency />
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
        <ToDelinquencyForm ref={formikRef} onFormSubmit={onFormSubmit} />
      </ConfirmDialog>
    </>
  );
};

export default MoveDelinquencyButton;
