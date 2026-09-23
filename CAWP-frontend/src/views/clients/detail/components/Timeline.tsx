import Timeline from "@/components/ui/Timeline";
import Avatar from "@/components/ui/Avatar";
import Badge from "@/components/ui/Badge";
import Card from "@/components/ui/Card";
import StatusPill from "@/components/shared/StatusPill";
import { HiOutlineClock } from "react-icons/hi";
import type { AvatarProps } from "@/components/ui/Avatar";
import {
  getComments,
  useAppDispatch,
  useAppSelector,
  deleteComments,
} from "../store";
import { useTranslation } from "react-i18next";
import { useEffect } from "react";
import dayjs from "dayjs";
import advancedFormat from "dayjs/plugin/advancedFormat";
import { HiOutlinePhoneOutgoing } from "react-icons/hi";
import Loading from "@/components/shared/Loading";
import DeleteButton from "@/components/shared/myComponents/deleteButton";
import { useState } from "react";

dayjs.extend(advancedFormat);

const DELETE_WINDOW_MS = 60 * 1000;

type TimelineAvatarProps = AvatarProps;

const TimelineAvatar = ({ children, ...rest }: TimelineAvatarProps) => {
  return (
    <Avatar {...rest} size={25} shape="circle">
      {children}
    </Avatar>
  );
};

const TimeLineComment = () => {
  const { t } = useTranslation();
  const { permissions, id: currentUserId } = useAppSelector((state) => state.auth.user);
  const [currentTime, setCurrentTime] = useState(Date.now());
  const hasDeleteAnyPermission = Boolean(
    (permissions as any)?.clints_comments_delete
  );

  const dispatch = useAppDispatch();
  const client = useAppSelector(
    (state) => state.crmCustomerDetails?.data?.clientData
  );
  const comments = useAppSelector(
    (state) => state.crmCustomerDetails?.data?.comments
  );

  const loadingComments = useAppSelector(
    (state) => state.crmCustomerDetails?.data?.loadingComments
  );

  // Function to check if user can delete comment
  const canDeleteComment = (comment: any) => {
    // If user has general delete permission, they can delete any comment
    if (hasDeleteAnyPermission) {
      return true;
    }

    // Check if it's the user's own comment and within 60 seconds
    const isOwnComment = comment.user?.id === currentUserId;
    const createdAt = new Date(comment.create_at);
    const timeDiffInSeconds = (currentTime - createdAt.getTime()) / 1000;
    
    return isOwnComment && timeDiffInSeconds <= 60;
  };

  // Function to get remaining time for delete
  const getRemainingDeleteTime = (comment: any) => {
    if (hasDeleteAnyPermission) {
      return null; // No time limit for users with delete permission
    }

    const isOwnComment = comment.user?.id === currentUserId;
    if (!isOwnComment) return null;

    const createdAt = new Date(comment.create_at);
    const timeDiffInSeconds = (currentTime - createdAt.getTime()) / 1000;
    const remainingSeconds = Math.max(0, 60 - timeDiffInSeconds);

    return remainingSeconds > 0 ? Math.ceil(remainingSeconds) : null;
  };

  useEffect(() => {
    dispatch(getComments(client?.id));
  }, [client]);

  // Update current time every second ONLY while at least one of the user's own
  // comments is still inside its delete window; the ticker stops itself once the
  // last window expires so the comment list is not re-rendered every second.
  useEffect(() => {
    if (hasDeleteAnyPermission) {
      return;
    }

    let lastDeadline = 0;
    for (const comment of (comments ?? []) as any[]) {
      if (comment?.user?.id === currentUserId) {
        const deadline =
          new Date(comment.create_at).getTime() + DELETE_WINDOW_MS;
        if (deadline > lastDeadline) {
          lastDeadline = deadline;
        }
      }
    }

    if (lastDeadline <= Date.now()) {
      return;
    }

    setCurrentTime(Date.now());
    const interval = setInterval(() => {
      const now = Date.now();
      setCurrentTime(now);
      if (now >= lastDeadline) {
        clearInterval(interval);
      }
    }, 1000);

    return () => clearInterval(interval);
  }, [comments, hasDeleteAnyPermission, currentUserId]);

  return (
    <Card>
      <div className="max-w-[900px]">
        <Loading loading={loadingComments} type="cover">
          <Timeline>
            {comments?.length > 0 ? (
              comments.map((comment, i) => {
                const remainingTime = getRemainingDeleteTime(comment);
                
                return (
                  <Timeline.Item
                    key={comment.id}
                    className="min-h-[44px] border-b border-gray-200 px-3 pt-4 transition-colors duration-150 last:border-b-0 hover:bg-gray-50"
                    media={
                      <TimelineAvatar className="bg-navy-600 text-white">
                        {comment?.user?.frist_name[0]}
                      </TimelineAvatar>
                    }
                  >
                    <div className="my-1 flex flex-wrap items-center gap-x-2 gap-y-2 text-[15px]">
                      <span className="font-extrabold text-navy-600 dark:text-gray-100">
                        {comment?.user?.frist_name +
                          " " +
                          comment?.user?.last_name}
                      </span>
                      <span className="text-gray-600">
                        {t("comments.contactMethodBy")}{" "}
                      </span>
                      <span className="font-extrabold text-navy-600 dark:text-gray-100">
                        {comment?.contact_method?.name}
                      </span>

                      <StatusPill
                        color={comment?.status?.color}
                        tone={
                          comment.status.is_active ? "success" : "danger"
                        }
                      >
                        {comment?.status.name}
                      </StatusPill>

                      <StatusPill
                        tone={
                          comment.reachability.name === "تم التوصل"
                            ? "success"
                            : "danger"
                        }
                      >
                        {comment?.reachability.name}
                      </StatusPill>

                      {comment.next_contact_date && (
                        <span className="text-[13.5px] text-gray-600 tabular-nums">
                          {t("comments.nextContactDate2") +
                            " " +
                            dayjs(new Date(comment.next_contact_date))
                              .locale("en")
                              .format("Do MMMM  h:mm a")}
                        </span>
                      )}

                      {/* Timer and Delete Button Container */}
                      <div className="flex items-center gap-2 ms-auto">
                        {remainingTime && remainingTime > 0 && (
                          <span className="tag tag-warning tabular-nums">
                            <HiOutlineClock aria-hidden className="shrink-0" />
                            {remainingTime}s
                          </span>
                        )}

                        {canDeleteComment(comment) && (
                          <DeleteButton
                            row={comment.id}
                            fetchData={() => dispatch(getComments(client?.id))}
                            notificationFailedMessage="comments.deleteFailed"
                            notificationSuccessMessage="comments.deleteSuccess"
                            notificationTitle="comments.deleteTitle"
                            confirmDialogMessage="comments.deleteConfirm"
                            confirmDialogTitle="comments.deleteConfirmTitle"
                            api={deleteComments}
                          />
                        )}
                      </div>
                    </div>

                    <div className="mt-3 rounded-lg border border-gray-200 bg-gray-50 p-4 text-[15px] text-gray-700 dark:border-night-border dark:bg-night-raised">
                      <p>{comment?.comment}</p>
                    </div>

                    <div className="mt-2 text-[13.5px] text-gray-600 tabular-nums">
                      <span>
                        {dayjs(new Date(comment.create_at))
                          .locale("en")
                          .format("Do MMMM  h:mm A")}
                      </span>
                    </div>
                  </Timeline.Item>
                );
              })
            ) : (
              <div className="flex items-center justify-center h-64">
                <p className="text-[15px] text-gray-600">
                  {t("comments.noComments")}
                </p>
              </div>
            )}
          </Timeline>
        </Loading>
      </div>
    </Card>
  );
};

export default TimeLineComment;
