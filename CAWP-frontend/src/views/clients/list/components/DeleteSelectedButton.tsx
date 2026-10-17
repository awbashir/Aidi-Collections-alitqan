import React, { useState, useCallback, useEffect } from "react";
import Tooltip from "@/components/ui/Tooltip";
import { useTranslation } from "react-i18next";
import useThemeClass from "@/utils/hooks/useThemeClass";
import { HiOutlineTrash } from "react-icons/hi";
import {
  deleteClients,
  getClients,
  useAppSelector,
  useAppDispatch,
} from "../store";
import ToastNotification from "@/components/shared/myComponents/ToastNotification";
import ConfirmDialog from "@/components/shared/myComponents/ConfirmDialog";
import { openCallRefusal } from "@/utils/v2/openCallGuard";
import { Button } from "@/components/ui/Button";
import { useFetchClients } from "../hooks/GetClientHook";

const DeleteSelectedButton = ({
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
  const [isLoading, setIsLoading] = useState(false);

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
      ({ status } = await api(row));
    } catch (err) {
      // 409: selected cases have an open call; nothing was deleted (utils/v2/openCallGuard)
      ToastNotification(openCallRefusal(err) || t(notificationFailedMessage), t(notificationTitle), "danger");
      setLoading(false);
      setDialogOpen(false);
      return;
    }

    if (status) {
      setLoading(false);
      ToastNotification(
        t(notificationSuccessMessage),
        t(notificationTitle),
        "success"
      );
      setDialogOpen(false);
      fetchData();
    } else {
      ToastNotification(
        t(notificationFailedMessage),
        t(notificationTitle),
        "danger"
      );
      setDialogOpen(false);
    }
  };

  return (
    <>
      <Button
        variant="default"
        className="!text-red-600 dark:!text-red-500"
        loading={isLoading}
        onClick={onConfirmDialogOpen}
        size="sm"
        icon={<HiOutlineTrash />}
      >
        {t("delete")}
      </Button>

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

export default DeleteSelectedButton;
