import React, { useState, useRef } from "react";
import Tooltip from "@/components/ui/Tooltip";
import { useTranslation } from "react-i18next";
import useThemeClass from "@/utils/hooks/useThemeClass";
import { HiOutlinePencil } from "react-icons/hi";
import { deletePortfolios } from "../store";
import ToastNotification from "@/components/shared/myComponents/ToastNotification";
import ConfirmDialog from "@/components/shared/myComponents/ConfirmDialog";
import EditPaymentForm, { FormikRef } from "./form/EditPaymentForm";
import {
  setTableData,
  useAppDispatch,
  useAppSelector,
  getPortfolios,
  deactivatePortfolio,
  activatePortfolio,
  Portfolio,
  setFilterData,
  setSearchOpen,
  setSearchClose,
} from "../store";

const EditPaymentButton = ({
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

  const [dialogOpen, setDialogOpen] = useState(false);
  const [loading, setLoading] = useState(false);

  const dispatch = useAppDispatch();

  const { page, pageSize, sort, query, total } = useAppSelector(
    (state) => state.paymentsList.data.tableData
  );

  const filterData = useAppSelector(
    (state) => state.paymentsList.data.filterData
  );

  const onConfirmDialogOpen = () => {
    setDialogOpen(true);
  };
  const onConfirmDialogClose = () => {
    setDialogOpen(false);
  };

  const handleConfirm = async () => {
    formikRef.current?.submitForm();
    formikRef.current.values.id = row.id;

    setLoading(true);
    const { id } = await api(formikRef.current.values);

    if (id) {
      setLoading(false);
      dispatch(
        getPortfolios({
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
      <Tooltip title={t("payments.edit")}>
        <span
          className={`cursor-pointer hover:text-azure-600 p-2 `}
          onClick={onConfirmDialogOpen}
        >
          <HiOutlinePencil />
        </span>
      </Tooltip>

      <ConfirmDialog
        isOpen={dialogOpen}
        type="edit"
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
        <EditPaymentForm
          ref={formikRef}
          onFormSubmit={onFormSubmit}
          id={row.id}
        />
      </ConfirmDialog>
    </>
  );
};

export default EditPaymentButton;
