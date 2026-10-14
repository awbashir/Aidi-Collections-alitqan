/**
 * Scroll restoration for list screens that open a case in the same tab.
 *
 * The position is kept per list URL (pathname + search + hash, without the
 * hash keys that only mark a row, e.g. `case=`) in sessionStorage, so it
 * survives the round trip to the case screen and a reload of the list:
 *
 *   save      while the user scrolls (throttled), right before a case is
 *             opened (`saveListScrollNow`, called by `openCaseInApp`) and when
 *             the list unmounts.
 *   restore   once per visit: early, if the rows the store still holds already
 *             make the page tall enough, and again when the list has finished
 *             loading (unless the user has scrolled in the meantime).
 *
 * The window is the page scroller. A list can also attach `rootRef` to its
 * wrapper; the table's own horizontal scroller inside it (`.overflow-x-auto`)
 * is then saved and restored as well.
 */
import { useCallback, useEffect, useLayoutEffect, useRef } from "react";
import { useLocation } from "react-router-dom";

type SavedScroll = {
  x: number;
  y: number;
  /** The table scroller's offsets, when there is one. */
  cx?: number;
  cy?: number;
  /** Saved at (ms), to drop the oldest entries. */
  t: number;
};

const STORAGE_KEY = "cawp.listScroll.v1";
/** Hash keys that mark a row rather than the list view. */
const DEFAULT_IGNORED_HASH_KEYS = ["case"] as const;
const MAX_ENTRIES = 30;
const SAVE_THROTTLE_MS = 200;
/** A list that never reports loading still restores after this. */
const SETTLE_FALLBACK_MS = 1200;
/** Give up waiting for the list to load. */
const GIVE_UP_MS = 15000;

const readAll = (): Record<string, SavedScroll> => {
  try {
    const raw = window.sessionStorage.getItem(STORAGE_KEY);
    const parsed = raw ? JSON.parse(raw) : null;
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
};

const writeEntry = (key: string, entry: SavedScroll) => {
  try {
    const all = readAll();
    all[key] = entry;
    const keys = Object.keys(all);
    if (keys.length > MAX_ENTRIES) {
      keys
        .sort((a, b) => (all[a]?.t ?? 0) - (all[b]?.t ?? 0))
        .slice(0, keys.length - MAX_ENTRIES)
        .forEach((k) => delete all[k]);
    }
    window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(all));
  } catch {
    // Storage blocked or full: scrolling back is a convenience only.
  }
};

/** The list's storage key: its URL without the row-marker hash keys. */
export function listScrollKey(
  loc: { pathname: string; search: string; hash: string },
  ignoreHashKeys: readonly string[] = DEFAULT_IGNORED_HASH_KEYS
): string {
  const body = (loc.hash || "").replace(/^#/, "");
  const kept = body
    .split("&")
    .filter((part) => {
      if (!part) return false;
      const name = part.split("=")[0];
      return !ignoreHashKeys.includes(name);
    })
    .join("&");
  return `${loc.pathname}${loc.search || ""}${kept ? `#${kept}` : ""}`;
}


/* The mounted list (there is one list screen at a time) registers its saver
 * here, so the case opener can save without knowing which list it is on. */
let activeSaver: (() => void) | null = null;

/** Save the mounted list's scroll position now (before leaving it). */
export function saveListScrollNow(): void {
  activeSaver?.();
}

export type ListScrollRestoreOptions = {
  /** The list's loading flag; the final restore waits for it to settle. */
  loading: boolean;
  /** Hash keys left out of the storage key (default: `case`). */
  ignoreHashKeys?: readonly string[];
};

function useListScrollRestore({
  loading,
  ignoreHashKeys = DEFAULT_IGNORED_HASH_KEYS,
}: ListScrollRestoreOptions) {
  const location = useLocation();
  const key = listScrollKey(location, ignoreHashKeys);

  const keyRef = useRef(key);
  keyRef.current = key;
  const rootRef = useRef<HTMLDivElement | null>(null);

  /* Read once per visit: the position to go back to, if any. */
  const pendingRef = useRef<SavedScroll | null | undefined>(undefined);
  if (pendingRef.current === undefined) {
    pendingRef.current = readAll()[key] ?? null;
  }
  /** Saving starts only once the restore is over, so the short page shown
   *  while loading cannot overwrite the saved position with 0. */
  const doneRef = useRef(pendingRef.current === null);
  const userMovedRef = useRef(false);
  const sawLoadingRef = useRef(loading);

  const tableScroller = () =>
    (rootRef.current?.querySelector(".overflow-x-auto") as HTMLElement | null) ??
    null;

  const save = useCallback(() => {
    if (!doneRef.current) return;
    const scroller = tableScroller();
    writeEntry(keyRef.current, {
      x: window.scrollX,
      y: window.scrollY,
      ...(scroller ? { cx: scroller.scrollLeft, cy: scroller.scrollTop } : {}),
      t: Date.now(),
    });
  }, []);

  const apply = (target: SavedScroll) => {
    window.scrollTo(target.x || 0, target.y || 0);
    const scroller = tableScroller();
    if (scroller && (target.cx !== undefined || target.cy !== undefined)) {
      scroller.scrollLeft = target.cx ?? 0;
      scroller.scrollTop = target.cy ?? 0;
    }
  };

  const finish = useCallback(() => {
    const target = pendingRef.current;
    pendingRef.current = null;
    if (target && !userMovedRef.current) {
      apply(target);
    }
    doneRef.current = true;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /* Early restore: the store usually still holds the rows of this page, so
   * the page is already tall enough before the refetch lands. */
  useLayoutEffect(() => {
    const target = pendingRef.current;
    if (!target) return;
    const room =
      document.documentElement.scrollHeight - window.innerHeight + 1;
    if (room >= (target.y || 0)) apply(target);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /* Final restore, once the list has loaded. */
  useEffect(() => {
    if (doneRef.current) return;
    if (loading) {
      sawLoadingRef.current = true;
      return;
    }
    if (sawLoadingRef.current) {
      const frame = window.requestAnimationFrame(finish);
      return () => window.cancelAnimationFrame(frame);
    }
    // Not loading yet: the fetch may start on the next render. Restore
    // anyway if it never does.
    const timer = window.setTimeout(finish, SETTLE_FALLBACK_MS);
    return () => window.clearTimeout(timer);
  }, [loading, finish]);

  useEffect(() => {
    const giveUp = window.setTimeout(() => {
      if (!doneRef.current) {
        pendingRef.current = null;
        doneRef.current = true;
      }
    }, GIVE_UP_MS);
    return () => window.clearTimeout(giveUp);
  }, []);

  /* The user scrolling by hand before the restore wins over the restore. */
  useEffect(() => {
    if (doneRef.current) return;
    const moved = () => {
      userMovedRef.current = true;
    };
    const events = ["wheel", "touchmove", "keydown", "mousedown"] as const;
    events.forEach((name) =>
      window.addEventListener(name, moved, { passive: true, once: true })
    );
    return () =>
      events.forEach((name) => window.removeEventListener(name, moved));
  }, []);

  /* Throttled save while scrolling. */
  useEffect(() => {
    let timer: number | null = null;
    const onScroll = () => {
      if (timer !== null) return;
      timer = window.setTimeout(() => {
        timer = null;
        save();
      }, SAVE_THROTTLE_MS);
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      window.removeEventListener("scroll", onScroll);
      if (timer !== null) window.clearTimeout(timer);
    };
  }, [save]);

  /* Register as the mounted list; save once more on the way out. A layout
   * cleanup runs before the list's DOM is removed, so the offsets are still
   * the list's. */
  useLayoutEffect(() => {
    activeSaver = save;
    return () => {
      save();
      if (activeSaver === save) activeSaver = null;
    };
  }, [save]);

  return { rootRef, saveNow: save };
}

export default useListScrollRestore;
