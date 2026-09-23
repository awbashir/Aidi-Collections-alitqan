import ApiService from "./ApiService";
import type { AxiosResponse } from "axios";

/* Background-job contract (backend §7).
 *
 * The 14 bulk routes accept `?async=1` (or `{"async": true}` in the body).
 * Validation stays synchronous — a bad file, a missing contract or an empty id
 * list still returns the legacy 201/400/404 body immediately. Work that passes
 * validation is queued and the route answers:
 *
 *   202 { status: true, job_id: "<hex>", message: "…", async: true }
 *
 * `GET /jobs/<id>` then reports progress. When `status === "done"`, `result` is
 * byte-identical to the body the synchronous call would have returned, so the
 * dialogs can keep the exact code they already had. */

export type JobStatus = "queued" | "running" | "done" | "failed";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type JobResult = any;

export type Job<R = JobResult> = {
  id: string;
  name: string;
  owner: string;
  status: JobStatus;
  /** 0–100, moves per 500-row batch. */
  progress: number;
  /** "<done>/<total>" while running, null before it starts. */
  message: string | null;
  /** null until `status === "done"`. */
  result: R | null;
  /** `str(exception)` when `status === "failed"`. */
  error: string | null;
  created_at?: number;
  started_at?: number | null;
  finished_at?: number | null;
};

/** The 202 envelope returned when work was queued. */
export type JobAccepted = {
  status: boolean;
  job_id: string;
  message: string;
  async: boolean;
};

//get /jobs/<job_id>

export function apiGetJob<R = JobResult>(id: string) {
  return ApiService.fetchData<Job<R>>({
    url: `/jobs/${id}`,
    method: "get",
  });
}

/** The job id when the response is a 202 "queued" envelope, else null — which
 *  is how a dialog tells "poll for progress" from "this is the final body"
 *  (an older backend without the async flag simply answers 200/201). */
export function acceptedJobId(
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  response?: AxiosResponse<any> | null,
): string | null {
  if (!response || response.status !== 202) {
    return null;
  }
  const body = response.data as Partial<JobAccepted> | undefined;
  return typeof body?.job_id === "string" && body.job_id ? body.job_id : null;
}

export type AsyncImportOption = { async?: boolean };

/** Per-call Axios config for the Excel import routes.
 *
 *  Async: the route answers 202 in well under a second, so the default 2-minute
 *  cap applies. Synchronous: the row loop can legitimately run for minutes, so
 *  the call keeps its `timeout: 0` opt-out. The query string wins over the body
 *  on the backend, which is why the flag travels in `params`. */
export function asyncImportConfig(options?: AsyncImportOption) {
  return options?.async ? { params: { async: 1 } } : { timeout: 0 };
}
