import { useEffect, useState } from "react";
import {
  emptyJobSnapshot,
  getJobSnapshot,
  subscribeJob,
  trackJob,
} from "@/services/jobTracker";
import type { JobSnapshot } from "@/services/jobTracker";
import type { Job } from "@/services/JobsService";

export type UseJobReturn = {
  job: Job | null;
  /** Polling error: an expired (404) or unreachable job. */
  error: string | null;
  expired: boolean;
  isQueued: boolean;
  isRunning: boolean;
  isDone: boolean;
  isFailed: boolean;
};

/** Watch a background job. Polls every 1500 ms while it is queued or running
 *  and stops on done / failed / 404. The timer lives in the `jobTracker`
 *  singleton, so unmounting only removes this view's subscription — the job
 *  keeps being polled and its completion callback still fires. */
export default function useJob(jobId?: string | null): UseJobReturn {
  const [snapshot, setSnapshot] = useState<JobSnapshot>(() =>
    jobId ? getJobSnapshot(jobId) : emptyJobSnapshot(),
  );

  useEffect(() => {
    if (!jobId) {
      setSnapshot(emptyJobSnapshot());
      return;
    }
    trackJob(jobId);
    setSnapshot(getJobSnapshot(jobId));
    return subscribeJob(jobId, setSnapshot);
  }, [jobId]);

  const status = snapshot.job?.status;

  return {
    job: snapshot.job,
    error: snapshot.error,
    expired: snapshot.expired,
    isQueued: !!jobId && (status === "queued" || (!status && !snapshot.settled)),
    isRunning: !!jobId && !snapshot.settled,
    isDone: status === "done",
    isFailed: status === "failed" || snapshot.expired,
  };
}
