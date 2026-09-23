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
import { BulkMenu } from "@/views/phase2/bulk/entry";
import { useAppSelector as useRootSelector } from "@/store";

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
  const { permissions } = useRootSelector((state) => state.auth.user);
  const canArchive = Boolean((permissions as Record<string, boolean> | undefined)?.clints_move_archive);
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

      {/* Phase 2: reviewed portfolio actions (preview, then explicit
       * confirmation). The direct legacy archive stays as "classic". */}
      <BulkMenu
        compact
        testId={`p2-portfolio-bulk-${row.id}`}
        title={t("p2.entry.portfolioMenu")}
        groups={[
          {
            entries: [
              {
                kind: "archive",
                label: t("p2.entry.archivePortfolio"),
                testId: "p2-entry-archive-portfolio",
                launch: {
                  from: { path: "/portfolios", labelKey: "p2.back.portfolios" },
                  source: "portfolio",
                  portfolioId: row.id,
                  portfolioName: row.name,
                },
              },
              {
                kind: "round_robin",
                label: t("p2.entry.roundRobinPortfolio"),
                testId: "p2-entry-rr-portfolio",
                launch: {
                  from: { path: "/portfolios", labelKey: "p2.back.portfolios" },
                  portfolioId: row.id,
                  portfolioName: row.name,
                  params: { portfolio_id: row.id },
                },
              },
            ],
          },
        ]}
      />

      {/* Portfolio-wide archive needs clints_move_archive (decision 2026-09-24). */}
      {canArchive && (
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
      )}

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
