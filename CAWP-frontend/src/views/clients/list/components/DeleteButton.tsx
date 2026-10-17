import React, { useState } from "react";
import Tooltip from "@/components/ui/Tooltip";
import { useTranslation } from "react-i18next";
import useThemeClass from "@/utils/hooks/useThemeClass";
import { HiOutlineTrash } from "react-icons/hi";
import { deleteClients } from "../store";
import ToastNotification from "@/components/shared/myComponents/ToastNotification";
import ConfirmDialog from "@/components/shared/myComponents/ConfirmDialog";
import { openCallRefusal } from "@/utils/v2/openCallGuard";
import { useFetchClients } from "../hooks/GetClientHook";

const DeleteButton = ({
  row,
  notificationSuccessMessage,
  notificationFailedMessage,
  notificationTitle,
  confirmDialogMessage,
  confirmDialogTitle,
  api,
}) => {
  const { t } = useTranslation();
  const { textTheme } = useThemeClass();
  const fetchData = useFetchClients();

  const [dialogOpen, setDialogOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const onConfirmDialogOpen = () => {
    setDialogOpen(true);
  };
  const onConfirmDialogClose = () => {
    setDialogOpen(false);
  };

  const handleConfirm = async () => {
    setLoading(true);
    let status;
    try {
      ({ status } = await api(row.id));
    } catch (err) {
      // 409: the case has an open call (utils/v2/openCallGuard)
      ToastNotification(openCallRefusal(err) || t(notificationFailedMessage), t(notificationTitle), "danger");
      setLoading(false);
      setDialogOpen(false);
      return;
    }

    if (status) {
      setLoading(false);
      fetchData();
      setDialogOpen(false);
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

  return (
    <>
      <Tooltip title={t("delete")}>
        <span
          className={`cursor-pointer text-red-600 p-2 `}
          onClick={onConfirmDialogOpen}
        >
          <HiOutlineTrash />
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
        <p>{t(confirmDialogMessage)}</p>
      </ConfirmDialog>
    </>
  );
};

export default DeleteButton;
