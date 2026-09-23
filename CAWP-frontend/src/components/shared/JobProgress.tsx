import classNames from "classnames";
import { useTranslation } from "react-i18next";
import Alert from "@/components/ui/Alert";
import Progress from "@/components/ui/Progress";
import { useConfig } from "@/components/ui/ConfigProvider";
import type { ReactNode } from "react";
import type { Job } from "@/services/JobsService";

export type JobProgressProps = {
  job: Job | null;
  /** Polling error: an expired (404) or unreachable job. */
  error?: string | null;
  expired?: boolean;
  isFailed?: boolean;
  isDone?: boolean;
  /** The "done" view. Anything the caller wants to show once `result` landed;
   *  defaults to a compact summary of `result.message`. */
  children?: ReactNode;
  className?: string;
};

/* Background-import progress, in the enterprise register: a muted eyebrow, the
 * job's own message, a navy line and a tabular percentage. No spinner, no
 * exclamation marks, no emoji. */
const JobProgress = (props: JobProgressProps) => {
  const { job, error, expired, isFailed, isDone, children, className } = props;
  const { t } = useTranslation();
  const { mode } = useConfig();

  const frame = classNames(
    "rounded-lg border border-gray-300 bg-gray-50 p-4",
    "dark:border-night-border dark:bg-night-raised",
    className,
  );

  if (isFailed || job?.status === "failed" || expired) {
    return (
      <div className={className}>
        <Alert type="danger" showIcon title={t("job.failedTitle")}>
          <span className="text-[13.5px]">
            {expired || error === "expired"
              ? t("job.expired")
              : job?.error || error || t("job.failed")}
          </span>
        </Alert>
      </div>
    );
  }

  if (isDone || job?.status === "done") {
    /* A business failure still finishes as `done`; the outcome lives in
     * `result.status`, so the default view reads it rather than assuming
     * success. Anything the caller passes wins over it. */
    const result = job?.result as
      | { status?: boolean; message?: string }
      | null
      | undefined;
    const succeeded = result?.status !== false;
    return (
      <div className={className}>
        {children ?? (
          <Alert
            type={succeeded ? "success" : "warning"}
            showIcon
            title={t("job.doneTitle")}
          >
            <span className="text-[13.5px]">
              {result?.message ?? t("job.done")}
            </span>
          </Alert>
        )}
      </div>
    );
  }

  const percent = Math.max(0, Math.min(100, Math.round(job?.progress ?? 0)));
  const queued = !job || job.status === "queued";

  return (
    <div className={frame}>
      <div className="text-[11px] font-extrabold uppercase tracking-[0.1em] text-gray-500 dark:text-night-muted">
        {t("job.background")}
      </div>
      <div className="mt-1 text-[13.5px] text-gray-700 dark:text-night-body">
        {queued ? t("job.queued") : job?.message || t("job.working")}
      </div>
      <div className="mt-3 flex items-center gap-3">
        <div className="min-w-0 flex-1">
          <Progress
            size="sm"
            percent={percent}
            showInfo={false}
            color={mode === "dark" ? "azure-500" : "navy-600"}
          />
        </div>
        <span className="shrink-0 text-[12.5px] font-extrabold tabular-nums text-navy-600 dark:text-night-body">
          {percent}%
        </span>
      </div>
    </div>
  );
};

export default JobProgress;
