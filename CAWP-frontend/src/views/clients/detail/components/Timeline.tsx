import Timeline from "@/components/ui/Timeline";
import Avatar from "@/components/ui/Avatar";
import Card from "@/components/ui/Card";
import Button from "@/components/ui/Button";
import Switcher from "@/components/ui/Switcher";
import StatusPill from "@/components/shared/StatusPill";
import {
  HiOutlineClock,
  HiOutlineArchive,
  HiOutlinePencilAlt,
  HiOutlineDocumentText,
} from "react-icons/hi";
import type { AvatarProps } from "@/components/ui/Avatar";
import {
  getComments,
  useAppDispatch,
  useAppSelector,
  deleteComments,
} from "../store";
import { useTranslation } from "react-i18next";
import { useEffect, useMemo, useState } from "react";
import dayjs from "dayjs";
import advancedFormat from "dayjs/plugin/advancedFormat";
import Loading from "@/components/shared/Loading";
import DeleteButton from "@/components/shared/myComponents/deleteButton";
import ToastNotification from "@/components/shared/myComponents/ToastNotification";
import { historyApi, lookups } from "@/services/v2";
import type { Note } from "@/services/v2";
import { usePermissions, useV2Query } from "@/utils/v2/hooks";
import { PlainBadge } from "@/components/v2/Badges";
import { InlineError } from "@/components/v2/States";
import NoteHistoryDialog from "@/views/phase1/history/NoteHistoryDialog";
import {
  CorrectNoteDialog,
  RetireNoteDialog,
} from "@/views/phase1/history/NoteDialogs";

dayjs.extend(advancedFormat);

const DELETE_WINDOW_MS = 60 * 1000;
const NOTES_PAGE_SIZE = 200;

type TimelineAvatarProps = AvatarProps;

/** The legacy `/comments/clint/<id>` row, as far as this timeline reads it. */
type LegacyComment = {
  id: string;
  comment: string;
  create_at: string;
  next_contact_date?: string | null;
  is_deleted?: boolean;
  user?: { id?: string; frist_name?: string; last_name?: string } | null;
  contact_method?: { name?: string } | null;
  status?: { name?: string; color?: string | null; is_active?: boolean } | null;
  /** 0068: only on notes recorded with a sub-status */
  sub_status?: { id?: string; name?: string; color?: string | null } | null;
  reachability?: { name?: string } | null;
};

const TimelineAvatar = ({ children, ...rest }: TimelineAvatarProps) => {
  return (
    <Avatar {...rest} size={25} shape="circle">
      {children}
    </Avatar>
  );
};

/**
 * Every v2 note of the case (all pages, retired included), keyed by id. The
 * legacy list stays the source of what is shown; the v2 rows add revision,
 * corrected and retired.
 */
async function loadAllNotes(caseId: string, signal: AbortSignal) {
  const byId = new Map<string, Note>();
  let page = 1;
  // eslint-disable-next-line no-constant-condition
  while (true) {
    const res = await historyApi.caseNotes(
      caseId,
      { include_retired: true, page, page_size: NOTES_PAGE_SIZE },
      signal
    );
    res.items.forEach((n) => byId.set(n.id, n));
    if (res.items.length === 0 || page * NOTES_PAGE_SIZE >= res.total) break;
    page += 1;
  }
  return byId;
}

const TimeLineComment = () => {
  const { t } = useTranslation();
  const { id: currentUserId } = useAppSelector((state) => state.auth.user);
  const perms = usePermissions();
  const [currentTime, setCurrentTime] = useState(Date.now());
  const hasDeleteAnyPermission = Boolean(perms.clints_comments_delete);
  const canCorrect = Boolean(perms.clints_comments_correct);
  const canRetire = Boolean(perms.clints_comments_retire);
  const canHistory = Boolean(perms.clints_history);

  const dispatch = useAppDispatch();
  const client = useAppSelector(
    (state) => state.crmCustomerDetails?.data?.clientData
  ) as { id?: string } | undefined;
  const comments = useAppSelector(
    (state) => state.crmCustomerDetails?.data?.comments
  ) as unknown as LegacyComment[] | undefined;

  const loadingComments = useAppSelector(
    (state) => state.crmCustomerDetails?.data?.loadingComments
  );

  const caseId = client?.id;
  // Reloaded whenever the legacy list reloads (a new note, a correction, a retirement).
  const notes = useV2Query(
    caseId ? (signal) => loadAllNotes(caseId, signal) : null,
    [caseId, comments]
  );
  const v2 = notes.data;

  const [showRetired, setShowRetired] = useState(false);
  const [correcting, setCorrecting] = useState<Note | null>(null);
  const [retiring, setRetiring] = useState<Note | null>(null);
  const [historyId, setHistoryId] = useState<string | null>(null);

  // Reachability names, for retired notes that only the v2 list still returns.
  const [reachNames, setReachNames] = useState<Record<string, string>>({});
  useEffect(() => {
    let alive = true;
    lookups
      .reachability()
      .then((opts) => {
        if (!alive) return;
        const map: Record<string, string> = {};
        opts.forEach((o) => {
          map[o.value] = o.label;
        });
        setReachNames(map);
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, []);

  const isRetired = (comment: LegacyComment) =>
    Boolean(comment.is_deleted || v2?.get(comment.id)?.retired);

  /* The legacy list may leave retired notes out (GET /comments/clint filters
     is_deleted); they still come from the v2 list and are shown behind the
     "show retired" toggle, in the same shape. */
  const all = useMemo(() => {
    const legacy = comments ?? [];
    if (!v2) return legacy;
    const known = new Set(legacy.map((c) => c.id));
    const extra: LegacyComment[] = [];
    v2.forEach((n) => {
      if (!n.retired || known.has(n.id)) return;
      extra.push({
        id: n.id,
        comment: n.text,
        create_at: n.created_at,
        next_contact_date: n.next_contact_date,
        is_deleted: true,
        user: { id: n.author?.id, frist_name: n.author?.name || "", last_name: "" },
        contact_method: n.contact_method,
        status: n.status ? { name: n.status.name } : null,
        sub_status: n.sub_status ? { id: n.sub_status.id, name: n.sub_status.name } : null,
        reachability: n.reachability_id
          ? { name: reachNames[n.reachability_id] }
          : null,
      });
    });
    if (extra.length === 0) return legacy;
    return [...legacy, ...extra].sort(
      (a, b) => new Date(b.create_at).getTime() - new Date(a.create_at).getTime()
    );
  }, [comments, v2, reachNames]);

  const retiredCount = useMemo(
    () => all.filter((c) => isRetired(c)).length,
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [all]
  );

  const visible = useMemo(
    () => all.filter((c) => showRetired || !isRetired(c)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [all, showRetired]
  );

  // Function to check if user can delete comment
  const canDeleteComment = (comment: LegacyComment) => {
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
  const getRemainingDeleteTime = (comment: LegacyComment) => {
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

  const reloadComments = () => {
    if (client?.id) dispatch(getComments(client.id as never));
  };

  useEffect(() => {
    reloadComments();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [client]);

  // Update current time every second ONLY while at least one of the user's own
  // comments is still inside its delete window; the ticker stops itself once the
  // last window expires so the comment list is not re-rendered every second.
  // Users who retire notes (clints_comments_retire) get Retire instead of the
  // hard delete, so they need no ticker.
  useEffect(() => {
    if (hasDeleteAnyPermission || canRetire) {
      return;
    }

    let lastDeadline = 0;
    for (const comment of comments ?? []) {
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
  }, [comments, hasDeleteAnyPermission, canRetire, currentUserId]);

  const afterV2Change = (message: string) => () => {
    ToastNotification(message, t("p1.notes.title"), "success");
    reloadComments();
    notes.reload();
  };

  const anyV2Action = canCorrect || canRetire || canHistory;

  return (
    <Card>
      <div className="max-w-[900px]" data-testid="p1-timeline">
        {(retiredCount > 0 || (anyV2Action && !!notes.error)) && (
          <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
            {retiredCount > 0 ? (
              <div className="flex items-center gap-2 text-[13.5px] text-gray-700">
                <span data-testid="p1-notes-show-retired">
                  <Switcher
                    checked={showRetired}
                    onChange={(checked) => setShowRetired(!checked)}
                  />
                </span>
                <span>
                  {t("p1.action.showRetired")}{" "}
                  <span className="tabular-nums text-gray-500">
                    ({retiredCount})
                  </span>
                </span>
              </div>
            ) : (
              <span />
            )}
            {anyV2Action && notes.error ? (
              <div className="w-full">
                <InlineError error={notes.error} />
              </div>
            ) : null}
          </div>
        )}
        <Loading loading={loadingComments} type="cover">
          <Timeline>
            {visible.length > 0 ? (
              visible.map((comment) => {
                const retired = isRetired(comment);
                const note = v2?.get(comment.id);
                const retireReplacesDelete = canRetire;
                const remainingTime = retireReplacesDelete
                  ? null
                  : getRemainingDeleteTime(comment);

                return (
                  <Timeline.Item
                    key={comment.id}
                    className={
                      "min-h-[44px] border-b border-gray-200 px-3 pt-4 transition-colors duration-150 last:border-b-0 hover:bg-gray-50" +
                      (retired ? " opacity-60" : "")
                    }
                    media={
                      <TimelineAvatar className="bg-navy-600 text-white">
                        {comment?.user?.frist_name?.[0]}
                      </TimelineAvatar>
                    }
                  >
                    <div
                      className="my-1 flex flex-wrap items-center gap-x-2 gap-y-2 text-[15px]"
                      data-testid="p1-note"
                      data-id={comment.id}
                      data-retired={retired ? "true" : "false"}
                    >
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
                        tone={comment.status?.is_active ? "success" : "danger"}
                      >
                        {comment?.status?.name}
                        {/* 0068: "Main › Sub" when the note has a sub-status */}
                        {comment?.sub_status?.name
                          ? ` › ${comment.sub_status.name}`
                          : null}
                      </StatusPill>

                      <StatusPill
                        tone={
                          comment.reachability?.name === "تم التوصل"
                            ? "success"
                            : "danger"
                        }
                      >
                        {comment?.reachability?.name}
                      </StatusPill>

                      {retired && (
                        <PlainBadge tone="neutral">
                          <span data-testid="p1-note-retired-badge">
                            {t("p1.notes.retired")}
                          </span>
                        </PlainBadge>
                      )}

                      {note?.corrected && (
                        <PlainBadge tone="info">
                          <span
                            data-testid="p1-note-corrected-badge"
                            title={String(
                              t("p1.notes.originalWas", {
                                text: note.original_text,
                              })
                            )}
                          >
                            {t("p1.notes.corrected", {
                              count: note.correction_count,
                            })}
                          </span>
                        </PlainBadge>
                      )}

                      {comment.next_contact_date && (
                        <span className="text-[13.5px] text-gray-600 tabular-nums">
                          {t("comments.nextContactDate2") +
                            " " +
                            dayjs(new Date(comment.next_contact_date))
                              .locale("en")
                              .format("Do MMMM  h:mm a")}
                        </span>
                      )}

                      {/* Timer, note actions and delete button */}
                      <div className="flex flex-wrap items-center gap-1 ms-auto">
                        {remainingTime && remainingTime > 0 && (
                          <span className="tag tag-warning tabular-nums">
                            <HiOutlineClock aria-hidden className="shrink-0" />
                            {remainingTime}s
                          </span>
                        )}

                        {canCorrect && !retired && (
                          <Button
                            size="xs"
                            variant="plain"
                            icon={<HiOutlinePencilAlt />}
                            disabled={!note}
                            onClick={() => note && setCorrecting(note)}
                            data-testid="p1-note-correct"
                          >
                            {t("p1.notes.correct")}
                          </Button>
                        )}

                        {canRetire && !retired && (
                          <Button
                            size="xs"
                            variant="plain"
                            icon={<HiOutlineArchive />}
                            disabled={!note}
                            onClick={() => note && setRetiring(note)}
                            data-testid="p1-note-retire"
                          >
                            {t("p1.notes.retire")}
                          </Button>
                        )}

                        {canHistory && (
                          <Button
                            size="xs"
                            variant="plain"
                            icon={<HiOutlineDocumentText />}
                            onClick={() => setHistoryId(comment.id)}
                            data-testid="p1-note-history-open"
                          >
                            {t("p1.action.history")}
                          </Button>
                        )}

                        {!retireReplacesDelete && !retired && canDeleteComment(comment) && (
                          <DeleteButton
                            row={comment.id}
                            fetchData={() => reloadComments()}
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
                      <p className={retired ? "line-through decoration-gray-400" : undefined}>
                        {comment?.comment}
                      </p>
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
                  {all.length > 0
                    ? t("p1.notes.allRetired")
                    : t("comments.noComments")}
                </p>
              </div>
            )}
          </Timeline>
        </Loading>
      </div>

      <CorrectNoteDialog
        note={correcting}
        onClose={() => setCorrecting(null)}
        onReload={() => {
          reloadComments();
          notes.reload();
        }}
        onDone={afterV2Change(t("p1.notes.correctedToast"))}
      />
      <RetireNoteDialog
        note={retiring}
        onClose={() => setRetiring(null)}
        onReload={() => {
          reloadComments();
          notes.reload();
        }}
        onDone={afterV2Change(t("p1.notes.retiredToast"))}
      />
      {canHistory && (
        <NoteHistoryDialog noteId={historyId} onClose={() => setHistoryId(null)} />
      )}
    </Card>
  );
};

export default TimeLineComment;
