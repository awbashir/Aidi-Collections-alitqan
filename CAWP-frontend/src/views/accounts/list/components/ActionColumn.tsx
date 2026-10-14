import React, { useCallback } from "react";
import useDirection from "@/utils/hooks/useDirection";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import useThemeClass from "@/utils/hooks/useThemeClass";
import {
  useAppDispatch,
  deletePayment,
  putPortfoliolsToArchive,
  putPayment,
} from "../store";
import Tooltip from "@/components/ui/Tooltip";
import { Link } from "react-router-dom";
import {
  HiOutlinePencil,
  HiOutlineUserGroup,
  HiArrowSmLeft,
  HiArrowSmRight,
  HiOutlineTrash,
  HiOutlineArchive,
} from "react-icons/hi";
import ToggleStatusButton from "./ToggleStatusButton";
import DeleteButton from "./DeleteButton";
import MoveFromArchiveButton from "./MoveFromArchiveButton";
import EditPaymentButton from "./EditPaymentButton";

import { LuArchiveRestore } from "react-icons/lu";
import { caseLink } from "@/utils/v2/links";
import { isPlainLeftClick, openCaseInApp } from "@/utils/v2/rowNavigation";

const ActionColumn = ({ row }: { row: Portfolio }) => {
  const { t } = useTranslation();
  const [direction, updateDirection] = useDirection();
  const { textTheme } = useThemeClass();
  const dispatch = useAppDispatch();
  const navigate = useNavigate();

  const onEdit = () => {
    dispatch(setDrawerOpen());
    dispatch(setSelectedUser(row));
  };

  const onDetail = useCallback(() => {
    navigate(`/users/${row.id}`);
  }, [navigate, row]);

  return (
    <div className="flex items-center justify-end gap-1 text-base">
      <Tooltip title={t("details")}>
        <Link
          className={`cursor-pointer p-2 hover:text-azure-600 font-semibold`}
          to={caseLink(String(row?.clint?.id ?? ""))}
          onClick={(e) => {
            if (row?.clint?.id && isPlainLeftClick(e)) {
              e.preventDefault();
              openCaseInApp(navigate, row.clint.id);
            }
          }}
        >
          <span>
            {direction === "rtl" ? <HiArrowSmLeft /> : <HiArrowSmRight />}
          </span>
        </Link>
      </Tooltip>

      <EditPaymentButton
        row={row}
        notificationTitle={"payments.edit"}
        notificationFailedMessage={"payments.editFail"}
        notificationSuccessMessage={"payments.editSuccess"}
        confirmDialogTitle={"payments.edit"}
        confirmDialogMessage={"portfolios.confirmArchive"}
        api={putPayment}
      />

      <DeleteButton
        row={row}
        notificationTitle={"payments.delete"}
        notificationFailedMessage={"payments.deleteFail"}
        notificationSuccessMessage={"payments.deleteSuccess"}
        confirmDialogTitle={"payments.delete"}
        confirmDialogMessage={"payments.confirmDelete"}
        api={deletePayment}
      />

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
