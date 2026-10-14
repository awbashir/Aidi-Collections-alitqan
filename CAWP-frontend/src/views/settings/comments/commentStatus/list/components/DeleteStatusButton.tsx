import { useState } from "react";
import { useTranslation } from "react-i18next";
import { HiOutlineTrash } from "react-icons/hi";
import Button from "@/components/ui/Button";
import Tooltip from "@/components/ui/Tooltip";
import ConfirmDialog from "@/components/shared/myComponents/ConfirmDialog";
import ToastNotification from "@/components/shared/myComponents/ToastNotification";
import { usePermissions } from "@/utils/v2/hooks";
import { deleteCommentStatus } from "../store";

/**
 * Soft delete of a note status (DELETE /comment_status/<id>, migration 0068), behind
 * `settings_comments_status_delete`. The confirmation says what happens: its sub-statuses
 * are deleted too, and notes already recorded keep showing the status.
 */
const DeleteStatusButton = ({
  id,
  name,
  isMain,
  onDeleted,
  variant = "icon",
}: {
  id: string;
  name: string;
  /** a main status: the dialog warns that its sub-statuses go with it */
  isMain: boolean;
  onDeleted: () => void;
  variant?: "icon" | "button";
}) => {
  const { t } = useTranslation();
  const permissions = usePermissions();
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);

  if (!permissions.settings_comments_status_delete) return null;

  const confirm = async () => {
    setLoading(true);
    try {
      const res = await deleteCommentStatus(id);
      if (res?.status) {
        setOpen(false);
        ToastNotification(
          t("p7.status.deleteSuccess"),
          t("p7.status.deleteTitle"),
          "success"
        );
        onDeleted();
      } else {
        ToastNotification(
          res?.message || t("p7.status.deleteFail"),
          t("p7.status.deleteTitle"),
          "danger"
        );
      }
    } catch {
      ToastNotification(
        t("p7.status.deleteFail"),
        t("p7.status.deleteTitle"),
        "danger"
      );
    } finally {
      setLoading(false);
    }
  };

  return (
    <>
      {variant === "icon" ? (
        <Tooltip title={t("delete")}>
          <button
            type="button"
            aria-label={String(t("p7.status.deleteTitle"))}
            className="cursor-pointer p-2 text-red-600 focus:outline-none focus-visible:ring-2 focus-visible:ring-azure-500 rounded"
            onClick={() => setOpen(true)}
            data-testid="p7-status-delete"
          >
            <HiOutlineTrash />
          </button>
        </Tooltip>
      ) : (
        <Button
          className="text-red-600"
          variant="plain"
          size="sm"
          icon={<HiOutlineTrash />}
          type="button"
          onClick={() => setOpen(true)}
          data-testid="p7-status-delete"
        >
          {t("delete")}
        </Button>
      )}
      <ConfirmDialog
        isOpen={open}
        type="danger"
        title={t("p7.status.deleteTitle")}
        cancelText={t("cancel")}
        confirmText={t("confirm")}
        confirmButtonColor="red-600"
        onClose={() => setOpen(false)}
        onRequestClose={() => setOpen(false)}
        onCancel={() => setOpen(false)}
        onConfirm={confirm}
        loading={loading}
      >
        <p className="font-extrabold text-navy-600 dark:text-gray-100">
          {name}
        </p>
        {isMain && <p className="mt-2">{t("p7.status.deleteChildren")}</p>}
        <p className="mt-2">{t("p7.status.deleteKeepsNotes")}</p>
      </ConfirmDialog>
    </>
  );
};

export default DeleteStatusButton;
