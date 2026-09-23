import { useCallback, useState, useEffect } from "react";
import { Formik, Field, FieldProps } from "formik";
import { FormItem } from "@/components/ui/Form";
import * as Yup from "yup";
import Button from "@/components/ui/Button";
import Dialog from "@/components/ui/Dialog";
import Input from "@/components/ui/Input";
import { useTranslation } from "react-i18next";
import { apiPutClintsBulkLock,apiPutClintsBulkUnlock } from "@/services/ClientsService";
import ToastNotification from "@/components/shared/myComponents/ToastNotification";
import { HiLockClosed, HiLockOpen } from "react-icons/hi";
import Tooltip from "@/components/ui/Tooltip";
import useThemeClass from "@/utils/hooks/useThemeClass";
import { useFetchClients } from "../hooks/GetClientHook";
import { useAppSelector } from "../store";

interface LockUnlockSelectedButtonProps {
  ids: number[];
  isLocking: boolean; // true for lock, false for unlock
}

interface FormModel {
  reason: string;
  client_ids: number[];
  action: 'lock' | 'unlock';
}

function LockUnlockSelectedButton({ ids, isLocking }: LockUnlockSelectedButtonProps) {
  const { t } = useTranslation();
  const { textTheme } = useThemeClass();
  const fetchData = useFetchClients();

  const [open, setOpen] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const handleClose = () => setOpen(false);

  const userId = useAppSelector((state) => state.auth.user.id);

  const handleOpen = () => {
    // Check if any clients are selected before opening dialog
    if (ids.length === 0) {
      ToastNotification(
        t("clients.noClientSelected"),
        t("warning"),
        "warning"
      );
      return;
    }
    setOpen(true);
  };

  // Define API function based on action
  const apiCall = isLocking ? apiPutClintsBulkLock : apiPutClintsBulkUnlock;

  const handleLockUnlock = async (values: FormModel) => {
    // Double check before API call
    if (ids.length === 0) {
      ToastNotification(
        t("clients.noClientSelected"),
        t("warning"),
        "warning"
      );
      return;
    }

    setIsLoading(true);
    
    const sendValue = {
      client_ids: ids,
      action: isLocking ? 'lock' : 'unlock',
      reason: values.reason,
      user_id: userId,
    };

    try {
      const response = await apiCall(sendValue);
      
      if (response.data.status) {
        fetchData();
        ToastNotification(
          t(isLocking ? "clients.lockClintsSuccess" : "clients.unlockClintsSuccess"),
          t(isLocking ? "clients.lockClints" : "clients.unlockClints"),
          "success"
        );
      } else {
        ToastNotification(
          t(isLocking ? "clients.lockClintsFail" : "clients.unlockClintsFail"),
          t(isLocking ? "clients.lockClints" : "clients.unlockClints"),
          "danger"
        );
      }
    } catch (error) {
      ToastNotification(
        t(isLocking ? "clients.lockClintsFail" : "clients.unlockClintsFail"),
        t(isLocking ? "clients.lockClints" : "clients.unlockClints"),
        "danger"
      );
    } finally {
      setIsLoading(false);
      handleClose();
    }
  };

  const initialValues: FormModel = {
    reason: "",
    client_ids: ids,
    action: isLocking ? 'lock' : 'unlock',
  };

  const SchemaLockUnlock = Yup.object().shape({
    reason: Yup.string().required(t("requiredField")),
    client_ids: Yup.array()
      .min(1, t("clients.noClientSelected"))
      .required(t("clients.noClientSelected")),
  });

  return (
    <div>
      <Tooltip title={t(isLocking ? "clients.lockSelected" : "clients.unlockSelected")}>
        <Button
          variant="default"
          loading={isLoading}
          onClick={handleOpen}
          size="sm"
          icon={isLocking ? <HiLockClosed /> : <HiLockOpen />}
          disabled={ids.length === 0} // Disable button if no clients selected
        >
          {t(isLocking ? "clients.lockSelected" : "clients.unlockSelected")}
        </Button>
      </Tooltip>

      <Dialog
        isOpen={open}
        onClose={handleClose}
        onRequestClose={handleClose}
      >
        <div>
          <h5 className="py-5">
            {t(isLocking ? "clients.confirmLockClints" : "clients.confirmUnlockClints")}
          </h5>
          <p className="mb-4">
            {t(isLocking ? "clients.confirmLockMessage" : "clients.confirmUnlockMessage", { count: ids.length })}
          </p>

          <Formik
            onSubmit={handleLockUnlock}
            initialValues={initialValues}
            validationSchema={SchemaLockUnlock}
          >
            {({
              values,
              errors,
              touched,
              handleChange,
              handleBlur,
              handleSubmit,
            }) => (
              <form onSubmit={handleSubmit}>
                <div className="grid">
                  <div className="grid">
                    <FormItem
                      asterisk
                      label={t("reason")}
                      invalid={errors.reason && touched.reason}
                      errorMessage={errors.reason}
                    >
                      <Field name="reason">
                        {({ field }: FieldProps<FormModel>) => (
                          <Input
                            {...field}
                            placeholder={t("enterReason")}
                            onChange={handleChange}
                            onBlur={handleBlur}
                          />
                        )}
                      </Field>
                    </FormItem>
                  </div>

                  <div className="my-5">
                    <div className="mt-4 ltr:text-right">
                      <div className="flex justify-end space-x-2">
                        <Button
                          className="ltr:mr-2 rtl:ml-2"
                          variant="solid"
                          color={isLocking ? "red-600" : undefined}
                          type="submit"
                          loading={isLoading}
                          disabled={ids.length === 0} // Disable submit button if no clients
                        >
                          {t(isLocking ? "clients.lockClients" : "clients.unlockClients")}
                        </Button>
                        <Button
                          variant="plain"
                          onClick={handleClose}
                          type="button"
                        >
                          {t("cancel")}
                        </Button>
                      </div>
                    </div>
                  </div>
                </div>
              </form>
            )}
          </Formik>
        </div>
      </Dialog>
    </div>
  );
}

export default LockUnlockSelectedButton;