import { apiGetJob } from "./JobsService";
import type { Job } from "./JobsService";

/* A deliberately tiny, store-free singleton.
 *
 * Polling lives here rather than in a component so that closing an import
 * dialog — or navigating away from the page — does not cancel the job or lose
 * its completion callback. React only ever *subscribes*; it never owns the
 * timer. Nothing here imports `@/store`, so the module stays usable from any
 * layer without a circular dependency. */

export const JOB_POLL_INTERVAL = 1500;

export type JobSnapshot = {
  job: Job | null;
  /** Set when polling itself failed (network, or an expired/foreign job id). */
  error: string | null;
  /** True once the job reached `done` / `failed`, or its state expired. */
  settled: boolean;
  /** True when the job id is unknown to the backend (404) — Redis TTL. */
  expired: boolean;
};

type Listener = (snapshot: JobSnapshot) => void;

type Entry = {
  snapshot: JobSnapshot;
  listeners: Set<Listener>;
  settleCallbacks: Set<(snapshot: JobSnapshot) => void>;
  timer: ReturnType<typeof setTimeout> | null;
  polling: boolean;
};

const entries = new Map<string, Entry>();

const PENDING: JobSnapshot = {
  job: null,
  error: null,
  settled: false,
  expired: false,
};

export const emptyJobSnapshot = (): JobSnapshot => ({ ...PENDING });

const emit = (entry: Entry) => {
  entry.listeners.forEach((listener) => listener(entry.snapshot));
};

const settle = (entry: Entry) => {
  entry.snapshot = { ...entry.snapshot, settled: true };
  if (entry.timer) {
    clearTimeout(entry.timer);
    entry.timer = null;
  }
  emit(entry);
  entry.settleCallbacks.forEach((callback) => callback(entry.snapshot));
  entry.settleCallbacks.clear();
};

const schedule = (id: string) => {
  const entry = entries.get(id);
  if (!entry || entry.snapshot.settled) {
    return;
  }
  entry.timer = setTimeout(() => {
    void poll(id);
  }, JOB_POLL_INTERVAL);
};

async function poll(id: string) {
  const entry = entries.get(id);
  if (!entry || entry.snapshot.settled) {
    return;
  }
  entry.polling = true;
  try {
    const response = await apiGetJob(id);
    const job = response.data;
    const current = entries.get(id);
    if (!current) {
      return;
    }
    current.polling = false;
    current.snapshot = {
      job,
      error: job.status === "failed" ? job.error || null : null,
      settled: false,
      expired: false,
    };
    if (job.status === "done" || job.status === "failed") {
      settle(current);
      return;
    }
    emit(current);
    schedule(id);
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
  } catch (error: any) {
    const current = entries.get(id);
    if (!current) {
      return;
    }
    current.polling = false;
    /* A 404 means the job state aged out of Redis (JOB_RESULT_TTL), which the
     * contract says to treat as "expired", not as a failure. Anything else is
     * a transport problem — keep polling, the API may just be restarting. */
    const status = error?.response?.status;
    if (status === 404) {
      current.snapshot = {
        ...current.snapshot,
        error: "expired",
        expired: true,
        settled: false,
      };
      settle(current);
      return;
    }
    schedule(id);
  }
}

/** Start (or join) polling for a job. Idempotent: calling it again with the
 *  same id only registers another completion callback. */
export function trackJob(
  id: string,
  onSettle?: (snapshot: JobSnapshot) => void,
): JobSnapshot {
  let entry = entries.get(id);
  if (!entry) {
    entry = {
      snapshot: emptyJobSnapshot(),
      listeners: new Set(),
      settleCallbacks: new Set(),
      timer: null,
      polling: false,
    };
    entries.set(id, entry);
    void poll(id);
  }
  if (onSettle) {
    if (entry.snapshot.settled) {
      onSettle(entry.snapshot);
    } else {
      entry.settleCallbacks.add(onSettle);
    }
  }
  return entry.snapshot;
}

/** Subscribe a view to a tracked job. Unsubscribing does **not** stop the
 *  poller — that is the whole point of keeping it out of React. */
export function subscribeJob(id: string, listener: Listener): () => void {
  const entry = entries.get(id) ?? (trackJob(id), entries.get(id));
  if (!entry) {
    return () => undefined;
  }
  entry.listeners.add(listener);
  return () => {
    entry.listeners.delete(listener);
  };
}

export function getJobSnapshot(id: string): JobSnapshot {
  return entries.get(id)?.snapshot ?? emptyJobSnapshot();
}

/** Drop a settled job from the map. Called when a dialog starts a new run. */
export function forgetJob(id: string) {
  const entry = entries.get(id);
  if (!entry) {
    return;
  }
  if (entry.timer) {
    clearTimeout(entry.timer);
  }
  entry.listeners.clear();
  entry.settleCallbacks.clear();
  entries.delete(id);
}
