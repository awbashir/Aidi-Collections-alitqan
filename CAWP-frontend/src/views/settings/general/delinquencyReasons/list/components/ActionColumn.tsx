import React, { useCallback } from "react";
import useDirection from "@/utils/hooks/useDirection";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import useThemeClass from "@/utils/hooks/useThemeClass";
import {
  useAppDispatch,
  deletePortfolios,
  putPortfoliolsToDelinquency,
} from "../store";
import Tooltip from "@/components/ui/Tooltip";
import { Link } from "react-router-dom";
import {
  HiOutlinePencil,
  HiOutlineUserGroup,
  HiArrowSmLeft,
  HiOutlineTrash,
  HiOutlineDelinquency,
} from "react-icons/hi";
import ToggleStatusButton from "./ToggleStatusButton";
import DeleteButton from "./DeleteButton";

/* No DELETE route exists for this entity in CAWP-API (checked 2026-09-24). */
const DELETE_ROUTE_EXISTS = false;

const ActionColumn = ({
  row,
  fetchadata,
}: {
  row: Portfolio;
  fetchadata: void;
}) => {
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
      {/*  <Tooltip title={t("details")}>
        <Link
          className={`cursor-pointer p-2 hover:text-azure-600 font-semibold`}
          to={`/users/details?id=${row.id}`}
        >
          <span>
            {direction === "rtl" ? <HiArrowSmLeft /> : <HiArrowSmRight />}
          </span>
        </Link>
      </Tooltip> */}

      <Tooltip title={t("edit")}>
        <Link
          className={`cursor-pointer p-2 hover:text-azure-600`}
          to={`/settings/general/delinquencyReasons/edit?id=${row.id}`}
        >
          <span>
            <HiOutlinePencil />
          </span>
        </Link>
      </Tooltip>

      {/* Phase 3 (review/phase3-admin.md §6): this button called
       * DELETE /portfolios/<this row's id>, not a delete of the row itself. The
       * API has no delete route for delinquency reasons, so the button is hidden until one
       * exists (kept in code, not removed). */}
      {DELETE_ROUTE_EXISTS && (
        <DeleteButton
          row={row}
          fetchData={fetchadata}
          notificationTitle={"portfolios.delete"}
          notificationFailedMessage={"portfolios.deleteFail"}
          notificationSuccessMessage={"portfolios.deleteSuccess"}
          confirmDialogTitle={"portfolios.delete"}
          confirmDialogMessage={"portfolios.confirmDelete"}
          api={deletePortfolios}
        />
      )}

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
