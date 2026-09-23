import toast from "@/components/ui/toast";
import Notification from "@/components/ui/Notification";

const PopNotification = (keyword: string) => {
  toast.push(
    <Notification type="success" duration={2500}>
      {keyword}
    </Notification>,
    {
      placement: "top-center",
    }
  );
};

export default PopNotification;
