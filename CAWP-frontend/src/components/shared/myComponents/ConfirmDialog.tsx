import {
  HiCheckCircle,
  HiOutlineInformationCircle,
  HiOutlineExclamation,
  HiOutlineExclamationCircle,
  HiOutlinePencil,
} from "react-icons/hi";
import Avatar from "@/components/ui/Avatar";
import Button from "@/components/ui/Button";
import Dialog from "@/components/ui/Dialog";
import { useEffect, useState } from "react";
import type { DialogProps } from "@/components/ui/Dialog";
import Loading from "@/components/shared/Loading";

type StatusType = "info" | "success" | "warning" | "danger";

interface ConfirmDialogProps extends DialogProps {
  cancelText?: ReactNode | string;
  confirmText?: ReactNode | string;
  confirmButtonColor?: string;
  type?: StatusType;
  title?: ReactNode | string;
  onCancel?: () => void;
  onConfirm?: () => void;
  loading?: boolean;
}

const StatusIcon = ({ status }: { status: StatusType }) => {
  switch (status) {
    case "info":
      return (
        <Avatar
          className="bg-blue-100 text-blue-600 dark:bg-blue-500/20 dark:text-blue-100"
          shape="circle"
        >
          <span className="text-2xl">
            <HiOutlineInformationCircle />
          </span>
        </Avatar>
      );
    case "success":
      return (
        <Avatar
          className="bg-emerald-100 text-emerald-600 dark:bg-emerald-500/20 dark:text-emerald-100"
          shape="circle"
        >
          <span className="text-2xl">
            <HiCheckCircle />
          </span>
        </Avatar>
      );
    case "warning":
      return (
        <Avatar
          className="text-amber-600 bg-amber-100 dark:text-amber-100"
          shape="circle"
        >
          <span className="text-2xl">
            <HiOutlineExclamationCircle />
          </span>
        </Avatar>
      );
    case "danger":
      return (
        <Avatar
          className="text-red-600 bg-red-100 dark:text-red-100"
          shape="circle"
        >
          <span className="text-2xl">
            <HiOutlineExclamation />
          </span>
        </Avatar>
      );

    case "edit":
      return (
        <Avatar
          className="text-red-600 bg-red-100 dark:text-red-100"
          shape="circle"
        >
          <span className="text-2xl">
            <HiOutlinePencil />
          </span>
        </Avatar>
      );

    default:
      return null;
  }
};

const ConfirmDialog = (props: ConfirmDialogProps) => {
  const {
    type = "info",
    title,
    children,
    onCancel,
    onConfirm,
    cancelText = "Cancel",
    confirmText = "Confirm",
    confirmButtonColor,
    loading = false,
    ...rest
  } = props;

  const isDestructive =
    type === "danger" || !!confirmButtonColor?.startsWith("red");

  const handleCancel = () => {
    onCancel?.();
  };

  const handleConfirm = () => {
    onConfirm?.();
  };

  const [isLoading, setIsLoading] = useState(loading);

  useEffect(() => {
    setIsLoading(loading);
  }, [loading]);

  return (
    <Dialog contentClassName="pb-0 px-0" {...rest}>
      <Loading loading={isLoading} type="cover">
        <>
          <div className="px-6 pb-6 pt-2 flex">
            <div>
              <StatusIcon status={type} />
            </div>
            <div className="ms-4">
              <h5 className="mb-2 text-[15px] font-extrabold text-navy-600 dark:text-night-strong">{title}</h5>
              {children}
            </div>
          </div>
          <div className="flex justify-end gap-2 border-t border-gray-300 dark:border-night-border bg-gray-50 dark:bg-night-page px-6 py-3 rounded-bl-xl rounded-br-xl">
            <Button size="sm" variant="default" onClick={handleCancel}>
              {cancelText}
            </Button>
            <Button
              size="sm"
              /* A truly destructive confirm gets the solid destructive ramp
                 (#B3261E / #8F1E17 / #6E1712), not a red-tinted primary. */
              variant={isDestructive ? "destructive" : "solid"}
              color={isDestructive ? undefined : confirmButtonColor}
              onClick={handleConfirm}
            >
              {confirmText}
            </Button>
          </div>
        </>
      </Loading>
    </Dialog>
  );
};

export default ConfirmDialog;
