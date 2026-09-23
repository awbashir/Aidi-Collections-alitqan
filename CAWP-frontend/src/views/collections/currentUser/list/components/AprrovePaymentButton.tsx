import React, { useState, useRef } from "react";
import Tooltip from "@/components/ui/Tooltip";
import { useTranslation } from "react-i18next";
import useThemeClass from "@/utils/hooks/useThemeClass";
import { HiOutlineArchive } from "react-icons/hi";
import { deletePortfolios } from "../store";
import ToastNotification from "@/components/shared/myComponents/ToastNotification";
import ConfirmDialog from "@/components/shared/myComponents/ConfirmDialog";
import RejectPaymentForm, { FormikRef } from "./form/RejectPaymentForm";
import { HiCheck } from "react-icons/hi";
import { HiX } from "react-icons/hi";
import {
  apiPutApprovePayment,
  apiPutRejectPayment,
} from "@/services/CollectionsService";
import {
  getPaymentsbyCurrentUser,
  useAppDispatch,
  useAppSelector,
} from "../store";
import StatusPill from "@/components/shared/StatusPill";

const AprrovePaymentButton = ({
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
  const dispatch = useAppDispatch();

  const { page, pageSize, sort, query, total } = useAppSelector(
    (state) => state.currentUser_paymentsList.data.tableData
  );

  const filterData = useAppSelector(
    (state) => state.currentUser_paymentsList.data.filterData
  );

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
    const { approval_status } = await api({
      id: row.id,
      approval_comment: formikRef.current?.values.approval_comment,
    });

    if (!approval_status) {
      setLoading(false);
      ToastNotification(
        t(notificationSuccessMessage),
        t(notificationTitle),
        "success"
      );
      dispatch(
        getPaymentsbyCurrentUser({
          page,
          pageSize,
          sort,
          query,
          filterData,
        })
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

  // if approval status is null show approve button and reject button
  if (row.approval_status === null) {
    return (
      <>
        <div className="grid grid-cols-2 gap-4">
          <Tooltip title={t("accept")}>
            <div
              onClick={() => {
                apiPutApprovePayment(row.id)
                  .then((response) => {
                    //if response 200
                    if (response.status === 200) {
                      dispatch(
                        getPaymentsbyCurrentUser({
                          page,
                          pageSize,
                          sort,
                          query,
                          filterData,
                        })
                      );
                      ToastNotification(
                        t(notificationSuccessMessage),
                        t(notificationTitle),
                        "success"
                      );
                    }

                    //if response 201
                    if (response.status === 201) {
                      ToastNotification(
                        t(notificationFailedMessage),
                        t(notificationTitle),
                        "danger"
                      );

                      dispatch(
                        getPaymentsbyCurrentUser({
                          page,
                          pageSize,
                          sort,
                          query,
                          filterData,
                        })
                      );
                    }
                  })
                  .catch((error) => {
                    console.log(error);
                    ToastNotification(
                      t(notificationFailedMessage),
                      t(notificationTitle),
                      "danger"
                    );
                  });
              }}
            >
              <HiCheck className="cursor-pointer text-green-600" />
            </div>
          </Tooltip>
          <Tooltip title={t("reject")}>
            <div onClick={onConfirmDialogOpen}>
              <HiX className="cursor-pointer text-red-600" />
            </div>
          </Tooltip>
        </div>
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
          <RejectPaymentForm ref={formikRef} onFormSubmit={onFormSubmit} />
        </ConfirmDialog>
      </>
    );
  }

  // if approval status is true make row green
  if (row.approval_status === true) {
    return (
      <div className="text-center">
        <StatusPill tone="success">تمت الموافقة</StatusPill>
      </div>
    );
  }

  // if approval status is false make row red
  if (row.approval_status === false) {
    return (
      <div className="text-center">
        <StatusPill tone="danger">تم الرفض</StatusPill>
      </div>
    );
  }

  /*  return (
    {
      


    }
    <>
      <Tooltip title={t("moveArchive")}>
        <span
          className={`cursor-pointer hover:text-azure-600 p-2 `}
          onClick={onConfirmDialogOpen}
        >
          <HiOutlineArchive />
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
        <ToArchiveForm ref={formikRef} onFormSubmit={onFormSubmit} />
      </ConfirmDialog>
    </>
  ); */
};

export default AprrovePaymentButton;
