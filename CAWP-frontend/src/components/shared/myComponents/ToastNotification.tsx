import toast from "@/components/ui/toast";
import Notification from "@/components/ui/Notification";
import { number } from "yup";

type StatusType = "info" | "success" | "warning" | "danger";

const ToastNotification = (
  message: string,
  title: string,
  type: StatusType,
  duration = 2500,
  closable = false
) => {
  toast.push(
    <Notification
      type={type}
      closable={closable}
      duration={duration}
      title={title}
    >
      {message}
    </Notification>,
    {
      placement: "top-center",
    }
  );
};

export default ToastNotification;
