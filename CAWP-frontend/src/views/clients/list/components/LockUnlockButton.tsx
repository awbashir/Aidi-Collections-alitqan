import React, { useState } from "react";
import { useTranslation } from "react-i18next";
import { HiLockClosed, HiLockOpen } from "react-icons/hi";
import Tooltip from "@/components/ui/Tooltip";
import Dialog from "@/components/ui/Dialog";
import Button from "@/components/ui/Button";
import useThemeClass from "@/utils/hooks/useThemeClass";
import { apiPutClintLockByID, apiPutClintUnlockByID } from "@/services/ClientsService";
import ToastNotification from "@/components/shared/myComponents/ToastNotification";
import { useFetchClients } from "../hooks/GetClientHook";
import Input from "@/components/ui/Input";


interface LockUnlockButtonProps {
  row: Client;
  notificationTitle: string;
  notificationFailedMessage: string;
  notificationSuccessMessage: string;
  confirmDialogTitle: string;
  confirmDialogMessage: string;
}

const LockUnlockButton: React.FC<LockUnlockButtonProps> = ({
  row,
  notificationTitle,
  notificationFailedMessage,
  notificationSuccessMessage,
  confirmDialogTitle,
  confirmDialogMessage,
}) => {
  const { t } = useTranslation();
  const fetchData = useFetchClients();
  const { textTheme } = useThemeClass();
  const [dialogIsOpen, setIsOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [reason, setReason] = useState("");

  const openDialog = () => {
    setIsOpen(true);

  };

  const onDialogClose = () => {
    setIsOpen(false);
    setReason(""); // Clear reason when dialog closes
  };

const handleLockUnlock = async () => {
    setLoading(true);
    
    try {
      const isLocking = !row.is_locked;
      
      if (isLocking) {
        const response = await apiPutClintLockByID(row.id, reason);
        if (response.status) {
          fetchData();
          ToastNotification(
            t("clients.lockClientSuccess"),
            t("clients.lockClient"),
            "success"
          );
        } else {
          ToastNotification(
            t("clients.lockClientFail"),
            t("clients.lockClient"),
            "danger"
          );
        }
      } else {
        const response = await apiPutClintUnlockByID(row.id, reason);
        if (response.status) {
          fetchData();
          ToastNotification(
            t("clients.unlockClientSuccess"),
            t("clients.unlockClient"),
            "success"
          );
        } else {
          ToastNotification(
            t("clients.unlockClientFail"),
            t("clients.unlockClient"),
            "danger"
          );
        }
      }
    } catch (error) {
      const action = row.is_locked ? "unlock" : "lock";
      ToastNotification(
        t(`clients.${action}ClientFail`),
        t(`clients.${action}Client`),
        "danger"
      );
    } finally {
      setLoading(false);
      setIsOpen(false);
      setReason(""); // Clear reason after action
    }
  };

  return (
    <>
      <Tooltip title={t(row.is_locked ? "clients.unlock" : "clients.lock")}>
        <span
          className={`cursor-pointer p-2 hover:text-azure-600`}
          onClick={openDialog}
        >
          {row.is_locked ? <HiLockOpen /> : <HiLockClosed />}
        </span>
      </Tooltip>

   <Dialog
        isOpen={dialogIsOpen}
        onClose={onDialogClose}
        onRequestClose={onDialogClose}
      >
        <h5 className="mb-4">{t(confirmDialogTitle)}</h5>
        <p className="mb-4">{t(confirmDialogMessage)}</p>
        
        <div className="mb-4">
          <label className="block text-sm font-medium mb-2">
            {t("reason")} {/* You may need to add this translation */}
          </label>
          <Input
            type="text"
            placeholder={t("enterReason")} // You may need to add this translation
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
        </div>

        <div className="text-end mt-6">
          <Button
            className="ltr:mr-2 rtl:ml-2"
            variant="plain"
            onClick={onDialogClose}
          >
            {t("cancel")}
          </Button>
          <Button
            variant="solid"
            onClick={handleLockUnlock}
            loading={loading}
          >
            {t("confirm")}
          </Button>
        </div>
      </Dialog>
    </>
  );
};

export default LockUnlockButton;