import React, { useCallback } from "react";
import useDirection from "@/utils/hooks/useDirection";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import useThemeClass from "@/utils/hooks/useThemeClass";
import {
  useAppDispatch,
  deletePortfolios,
  putPortfoliolsToArchive,
} from "../store";
import Tooltip from "@/components/ui/Tooltip";
import { Link } from "react-router-dom";
import {
  HiOutlinePencil,
  HiOutlineUserGroup,
  HiArrowSmLeft,
  HiOutlineTrash,
  HiOutlineArchive,
} from "react-icons/hi";
import ToggleStatusButton from "./ToggleStatusButton";
import DeleteButton from "./DeleteButton";
import MoveArchiveButton from "./MoveArchiveButton";

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
          to={`/portfolios/edit?id=${row.id}`}
        >
          <span>
            <HiOutlinePencil />
          </span>
        </Link>
      </Tooltip>

      <MoveArchiveButton
        row={row}
        fetchData={fetchadata}
        notificationTitle={"portfolios.archive"}
        notificationFailedMessage={"portfolios.archiveFail"}
        notificationSuccessMessage={"portfolios.archiveSuccess"}
        confirmDialogTitle={"portfolios.archive"}
        confirmDialogMessage={"portfolios.confirmArchive"}
        api={putPortfoliolsToArchive}
      />

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
