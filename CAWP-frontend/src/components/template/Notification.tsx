import { useEffect, useState, useCallback } from "react";
import classNames from "classnames";
import withHeaderItem from "@/utils/hoc/withHeaderItem";
import Avatar from "@/components/ui/Avatar";
import Dropdown from "@/components/ui/Dropdown";
import ScrollBar from "@/components/ui/ScrollBar";
import Spinner from "@/components/ui/Spinner";
import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import Tooltip from "@/components/ui/Tooltip";
import { Bell } from "lucide-react";
import {
  HiOutlineCalendar,
  HiOutlineClipboardCheck,
  HiOutlineBan,
  HiOutlineMailOpen,
  HiOutlineSpeakerphone
} from "react-icons/hi";
import { Link } from "react-router-dom";
import isLastChild from "@/utils/isLastChild";
import useTwColorByName from "@/utils/hooks/useTwColorByName";
import { useAppSelector } from "@/store";
import useResponsive from "@/utils/hooks/useResponsive";
import acronym from "@/utils/acronym";
import { useTranslation } from "react-i18next";
import appConfig from "@/configs/app.config";
//import { io } from "socket.io-client";
import dayjs from "dayjs";
import relativeTime from "dayjs/plugin/relativeTime";
import "dayjs/locale/ar";
import {
  apiGetCrmUsers,
  apiGetNotifications,
  apiPutNotificationRead,
} from "@/services/UserService";
import { list } from "postcss";

//const socket = io(appConfig.apiPrefix);

dayjs.extend(relativeTime);

export function formatDateToArabic(dateString: string) {
  return "منذ " + dayjs(dateString).locale("ar").fromNow(true);
}

type NotificationList = {
  id: string;
  target: string;
  description: string;
  date: string;
  image: string;
  type: number;
  location: string;
  locationLabel: string;
  status: string;
  is_read: boolean;
};

const notificationHeight = "h-72";
const imagePath = "/img/avatars/";

const GeneratedAvatar = ({ target }: { target: string }) => {
  const color = useTwColorByName();
  return (
    <Avatar shape="circle" className={`${color(target)}`}>
      {acronym(target)}
    </Avatar>
  );
};

const notificationTypeAvatar = (data: {
  type: number;
  target: string;
  image: string;
  status: string;
}) => {
  const { type, target, image, status } = data;
  switch (type) {
    case 0:
      if (image) {
        return <Avatar shape="circle" src={`${imagePath}${image}`} />;
      } else {
        return <GeneratedAvatar target={target} />;
      }
    case 1:
      return (
        <Avatar
          shape="circle"
          className="bg-info-tint text-info dark:bg-info/20 dark:text-azure-300"
          icon={<HiOutlineCalendar />}
        />
      );
    case 2:
      return (
        <Avatar
          shape="circle"
          className={
            status === "succeed"
              ? "bg-success-tint text-success dark:bg-success/20 dark:text-[#3FBF8F]"
              : "bg-danger-tint text-danger dark:bg-danger/20 dark:text-[#F1786E]"
          }
          icon={
            status === "succeed" ? (
              <HiOutlineClipboardCheck />
            ) : (
              <HiOutlineBan />
            )
          }
        />
      );
    case 3:
      return (
        <Avatar
          shape="circle"
          className={
            "bg-success-tint text-success dark:bg-success/20 dark:text-[#3FBF8F]"
          }
          icon={<HiOutlineSpeakerphone />}
        />
      );

    default:
      return <Avatar />;
  }
};

/* Unread is a 6px orange dot — one of the screen's sanctioned orange
   touchpoints. Never a red count pill. */
const NotificationToggle = ({
  className,
  count,
}: {
  className?: string;
  count: number;
}) => {
  return (
    <div className="relative flex items-center justify-center">
      <Bell size={20} strokeWidth={2} />
      {count > 0 && (
        <span
          className="absolute top-0 ltr:right-0 rtl:left-0 h-1.5 w-1.5 rounded-full bg-signal-500 dark:bg-signal-dark"
          title={`${count}`}
        />
      )}
    </div>
  );
};

const _Notification = ({ className }: { className?: string }) => {
  const [users, setUsers] = useState([]);
  const [unreadCount, setUnreadCount] = useState(0); // <-- Add this line

  const { t } = useTranslation();

  const getUserNameById = (id: string, users: list) => {
    const user = users.find((u) => u.id === id);
    return user ? user.name : id;
  };

  function getNotificationDescription(data, users, t) {
    return (
      <>
               {t("alert") + " "}
        
          {" " + t("by") + " "}
          <span className="font-bold">
            {getUserNameById(data.sender_user_id, users)}
          </span>
         <span>
             <br />
  بخصوص العميل {" "}
          <Link
            target="_blank"
            to={`/clients/details/details?id=${data?.client_id}`}
            className="text-link font-extrabold"
          >
            {data?.accountNumber}
          </Link>
  <br />
</span>
<span>
     {data.message}
    </span>
      </>
    );
  }

  const { id } = useAppSelector((state) => state.auth.user);
/* useEffect(() => {
  if (id) {
    socket.emit("join", id);
  }
}, [id]); */
  const [notificationList, setNotificationList] = useState<NotificationList[]>(
    []
  );
  const [unreadNotification, setUnreadNotification] = useState(false);
  const [noResult] = useState(false);
  const [loading] = useState(false);


  const { larger } = useResponsive();

  const direction = useAppSelector((state) => state.theme.direction);

  const getNotificationCount = async () => {
    // Fetch Notification count
    try {
      const response = await apiGetNotifications();
      // Assuming response.data.notifications is your array
      const list_notifications_Db = response.data || [];
      //loop through the list and convert each item by getNotificationDescription
      const data = list_notifications_Db.map((item: NotificationList) => {
        return {
          ...item,
          description: getNotificationDescription(item.data, users, t),
          type: item.data.type || 0, // Ensure type is set
          date: item.created_at || new Date().toISOString(), // Ensure date is set
          sender_user_id : item.sender_user_id || "", // Ensure sender_user_id is set

        };
      });
      setNotificationList(data); // <-- Set the full array at once

     

      const unread = (response.data || []).filter((n) => !n.is_read).length;
      setUnreadCount(unread);
    } catch (error) {
      // Handle error if needed
      console.error("Failed to fetch notifications", error);
    }
  };

  useEffect(() => {
    getNotificationCount();
  }, [users, t]);

  const onNotificationOpen = async () => {
    // Fetch NotificationList
  };

  const onMarkAllAsRead = useCallback(() => {
    const list = notificationList.map((item: NotificationList) => {
      if (!item.is_read) {
        item.is_read = true;
        // Call API to mark notification as read
        apiPutNotificationRead(item.id)
          .then(() => {
            console.log(`Notification ${id} marked as read`);
          })
          .catch((error) => {
            console.error(`Failed to mark notification ${id} as read`, error);
          });
      }
      return item;
    });
    setNotificationList(list);
    setUnreadCount(0); // <-- Reset count
  }, [notificationList]);

  const onMarkAsRead = useCallback(
    (id: string) => {
      const list = notificationList.map((item) => {
        if (item.id === id) {
          item.is_read = true;
          // Call API to mark notification as read
          apiPutNotificationRead(id)
            .then(() => {
              console.log(`Notification ${id} marked as read`);
            })
            .catch((error) => {
              console.error(`Failed to mark notification ${id} as read`, error);
            });
        }
        return item;
      });
      setNotificationList(list);
      // Update unread count
      const unread = list.filter((item) => !item.is_read).length;
      setUnreadCount(unread);
    },
    [notificationList]
  );

  /* useEffect(() => {
    socket.on("alarm", (data) => {
      //assign description to data.description
      var description = "alarm";
      data.description = description;

      data.description = (
        <>
          {t("alert") + " "}
        
          {" " + t("by") + " "}
          <span className="font-bold">
            {getUserNameById(data.sender_user_id, users)}
          </span>
         <span>
             <br />
  بخصوص العميل {" "}
          <Link
            target="_blank"
            to={`/clients/details/details?id=${data?.client_id}`}
            className="text-link font-extrabold"
          >
            {data?.accountNumber}
          </Link>
  <br />
</span>
<span>
     {data.message}
    </span>
        </>
      );
      setNotificationList((prev) => [data, ...prev]);
      setUnreadCount((prev) => prev + 1); // <-- Increment count
      // Handle new notification here, e.g.:
      // setNotificationList((prev) => [data, ...prev]);
      // setUnreadNotification(true);
    });

    return () => socket.off("alarm");
  }, [users, t]);
*/
  // ...existing code...

  const fetchUsers = async () => {
    const response = await apiGetCrmUsers();
    setUsers(response.data.users);
  };
  useEffect(() => {
    fetchUsers();
  }, []); 

  const ApiUnreadNotification = useCallback(async () => {
    try {
      const response = await apiGetNotifications();
      const unreadCount = response.data.filter((n) => !n.is_read).length;
      setUnreadNotification(unreadCount > 0);
    } catch (error) {
      console.error("Failed to fetch unread notifications", error);
    }
  }, []);

  return (
    <Dropdown
      renderTitle={
        <NotificationToggle count={unreadCount} className={className} />
      }
      menuClass="p-0 min-w-[280px] md:min-w-[340px] overflow-hidden"
      placement={larger.md ? "bottom-end" : "bottom-center"}
      onOpen={onNotificationOpen}
    >
      <Dropdown.Item variant="header">
        <div className="flex h-11 items-center justify-between border-b border-gray-200 px-4 dark:border-night-border">
          <h6 className="text-[15px] font-extrabold text-navy-600 dark:text-night-body">
            {t("Notifications")}
          </h6>
          <Tooltip title="Mark all as read">
            <Button
              variant="plain"
              shape="circle"
              size="sm"
              icon={<HiOutlineMailOpen className="text-xl" />}
              onClick={onMarkAllAsRead}
            />
          </Tooltip>
        </div>
      </Dropdown.Item>
      <div className={classNames("overflow-y-auto", notificationHeight)}>
        <ScrollBar direction={direction}>
          {notificationList.length > 0 &&
            notificationList.map((item, index) => (
              <div
                key={item.id}
                className={`relative flex min-h-[44px] cursor-pointer items-start px-4 py-3 text-[13.5px] text-gray-700 hover:bg-gray-50 active:bg-gray-100 dark:text-night-body dark:hover:bg-black dark:hover:bg-opacity-20 ${
                  !isLastChild(notificationList, index)
                    ? "border-b border-gray-200 dark:border-night-border"
                    : ""
                }`}
                onClick={() => onMarkAsRead(item.id)}
              >
                <div>{notificationTypeAvatar(item)}</div>
                <div className="ms-3 pe-5">
                  <div className="leading-relaxed">
                    {item.target && (
                      <span className="font-extrabold text-navy-600 dark:text-night-body">
                        {item.target}{" "}
                      </span>
                    )}
                    <span>{item.description}</span>
                  </div>
                  <span className="mt-0.5 block text-[12px] tabular-nums text-gray-500 dark:text-night-muted">
                    {formatDateToArabic(item.date)}
                  </span>
                </div>
                {!item.is_read && (
                  <Badge
                    className="absolute top-4 ltr:right-4 rtl:left-4"
                    innerClass="bg-signal-500"
                  />
                )}
              </div>
            ))}
          {loading && (
            <div
              className={classNames(
                "flex items-center justify-center",
                notificationHeight
              )}
            >
              <Spinner size={40} />
            </div>
          )}
          {(noResult || (!loading && notificationList.length === 0)) && (
            <div
              className={classNames(
                "flex items-center justify-center",
                notificationHeight
              )}
            >
              <div className="px-6 text-center">
                <Bell
                  size={20}
                  strokeWidth={1.5}
                  className="mx-auto mb-3 text-gray-400"
                  aria-hidden
                />
                <p className="text-[13.5px] font-extrabold text-navy-600 dark:text-night-body">
                  {t("NoNotifications", "لا توجد إشعارات")}
                </p>
                <p className="mt-1 text-[12.5px] text-gray-600 dark:text-night-muted">
                  {t("plzTryAgainLater", "يرجى المحاولة لاحقًا")}
                </p>
              </div>
            </div>
          )}
        </ScrollBar>
      </div>
      <Dropdown.Item variant="header">
        <div className="flex h-11 items-center justify-center border-t border-gray-200 px-4 dark:border-night-border">
          <Link
            to="/app/account/activity-log"
            className="text-link cursor-pointer text-[13.5px] font-extrabold"
          >
            {t("showAllNotifications")}
          </Link>
        </div>
      </Dropdown.Item>
    </Dropdown>
  );
};

const Notification = withHeaderItem(_Notification);

export default Notification;
