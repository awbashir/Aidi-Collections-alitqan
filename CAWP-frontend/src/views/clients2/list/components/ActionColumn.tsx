import React, { useCallback } from "react";
import useDirection from "@/utils/hooks/useDirection";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import useThemeClass from "@/utils/hooks/useThemeClass";
import { useAppDispatch, deleteClients, putClientlsToArchive } from "../store";
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
import MoveArchiveButton from "./MoveArchiveButton";
import MoveToArchiveButton from "./MoveToArchiveButton";
import SepcifyUsersOneButton from "./SepcifyUsersButton";
import { useAppSelector } from "@/store";
import { caseLink } from "@/utils/v2/links";
import { isPlainLeftClick, openCaseInApp } from "@/utils/v2/rowNavigation";

const ActionColumn = ({
  row,
  fetchadata,
}: {
  row: Client;
  fetchadata: void;
}) => {
  const { t } = useTranslation();
  const [direction, updateDirection] = useDirection();
  const { textTheme } = useThemeClass();
  const dispatch = useAppDispatch();
  const navigate = useNavigate();
  const { permissions } = useAppSelector((state) => state.auth.user);

  const onEdit = () => {
    dispatch(setDrawerOpen());
    dispatch(setSelectedUser(row));
  };

  const onDetail = useCallback(() => {
    navigate(`/users/${row.id}`);
  }, [navigate, row]);

  return (
    <div className="flex justify-end items-center text-[16px] text-gray-600 dark:text-night-muted">
      {
         row.is_archive ? null : (
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

         )

     

      }
     

      {/*   <Tooltip title={t("edit")}>
        <Link
          className={`cursor-pointer p-2 hover:text-azure-600`}
          to={`/clients/edit?id=${row.id}`}
        >
          <span>
            <HiOutlinePencil />
          </span>
        </Link>
      </Tooltip> */}

      {/*       <MoveArchiveButton
        row={row}
        fetchData={fetchadata}
        notificationTitle={"clients.archive"}
        notificationFailedMessage={"clients.archiveFail"}
        notificationSuccessMessage={"clients.archiveSuccess"}
        confirmDialogTitle={"clients.confirmArchive"}
        confirmDialogMessage={"clients.confirmArchive"}
        api={putClientlsToArchive}
      /> */}
   

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
