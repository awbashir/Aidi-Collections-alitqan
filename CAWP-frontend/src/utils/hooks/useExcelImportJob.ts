import { useCallback, useRef, useState } from "react";
import { acceptedJobId } from "@/services/JobsService";
import { forgetJob, trackJob } from "@/services/jobTracker";
import useJob from "./useJob";
import type { AxiosResponse } from "axios";
import type { JobResult } from "@/services/JobsService";

export type ExcelImportResultHandler = (
  body: JobResult,
  fromJob?: boolean,
) => void;

/** Wiring for an Excel-import dialog.
 *
 *  `handleResponse` is the single seam between the two transports: a 202 hands
 *  the work to the job poller, anything else (a 200/201 body from a backend
 *  without the async flag, or a synchronous validation failure) goes straight
 *  into the caller's existing result path. Because the job's `result` is
 *  byte-identical to the synchronous body, `onResult` is the same function in
 *  both cases.
 *
 *  The poller lives in the `jobTracker` module singleton, so closing the dialog
 *  mid-run neither cancels the job nor loses the completion toast. */
export default function useExcelImportJob(onResult: ExcelImportResultHandler) {
  const [jobId, setJobId] = useState<string | null>(null);
  const { job, error, expired, isQueued, isRunning, isDone, isFailed } =
    useJob(jobId);

  const handlerRef = useRef(onResult);
  handlerRef.current = onResult;

  const jobIdRef = useRef<string | null>(null);

  const reset = useCallback(() => {
    if (jobIdRef.current) {
      forgetJob(jobIdRef.current);
      jobIdRef.current = null;
    }
    setJobId(null);
  }, []);

  /** Returns true when the work was queued and progress is now being polled. */
  const handleResponse = useCallback(
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (response?: AxiosResponse<any> | null) => {
      const id = acceptedJobId(response);
      if (!id) {
        handlerRef.current(response?.data);
        return false;
      }
      jobIdRef.current = id;
      setJobId(id);
      trackJob(id, (snapshot) => {
        if (snapshot.job?.status === "done") {
          handlerRef.current(snapshot.job.result, true);
        }
      });
      return true;
    },
    [],
  );

  return {
    jobId,
    job,
    error,
    expired,
    isQueued,
    isRunning,
    isDone,
    isFailed,
    handleResponse,
    reset,
  };
}
