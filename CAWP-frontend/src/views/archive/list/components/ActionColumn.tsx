import React, { useCallback } from "react";
import useDirection from "@/utils/hooks/useDirection";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import useThemeClass from "@/utils/hooks/useThemeClass";
import {
  useAppDispatch,
  deletePortfolios,
  putPortfoliolsToArchive,
  putUnarchivePortfolios,
} from "../store";
import Tooltip from "@/components/ui/Tooltip";
import { Link } from "react-router-dom";
import {
  HiOutlinePencil,
  HiOutlineUserGroup,
  HiArrowSmLeft,
  HiOutlineTrash,
  HiOutlineArchive,
  HiArrowSmRight,
} from "react-icons/hi";
import ToggleStatusButton from "./ToggleStatusButton";
import DeleteButton from "./DeleteButton";
import MoveFromArchiveButton from "./MoveFromArchiveButton";
import { LuArchiveRestore } from "react-icons/lu";
import { caseLink } from "@/utils/v2/links";
import { isPlainLeftClick, openCaseInApp } from "@/utils/v2/rowNavigation";

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
      <Tooltip title={t("details")}>
        <Link
          className={`cursor-pointer p-2 hover:text-azure-600 font-semibold`}
          to={caseLink(row.id)}
          onClick={(e) => {
            if (isPlainLeftClick(e)) {
              e.preventDefault();
              openCaseInApp(navigate, row.id);
            }
          }}
        >
          <span>
            {direction === "rtl" ? <HiArrowSmLeft /> : <HiArrowSmRight />}
          </span>
        </Link>
      </Tooltip>

      <MoveFromArchiveButton
        row={row}
        fetchData={fetchadata}
        notificationTitle={"portfolios.archive"}
        notificationFailedMessage={"portfolios.archiveFail"}
        notificationSuccessMessage={"portfolios.archiveSuccess"}
        confirmDialogTitle={"portfolios.archive"}
        confirmDialogMessage={"portfolios.confirmArchive"}
        api={putUnarchivePortfolios}
      />

      {/*  <DeleteButton
        row={row}
        fetchData={fetchadata}
        notificationTitle={"portfolios.delete"}
        notificationFailedMessage={"portfolios.deleteFail"}
        notificationSuccessMessage={"portfolios.deleteSuccess"}
        confirmDialogTitle={"portfolios.delete"}
        confirmDialogMessage={"portfolios.confirmDelete"}
        api={deletePortfolios}
      />
 */}
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
