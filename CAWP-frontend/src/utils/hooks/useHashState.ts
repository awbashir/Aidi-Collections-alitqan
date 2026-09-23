/**
 * URL-hash list state.
 *
 * Keeps a list screen's state (filters, sort, page, page size, selected case)
 * in `location.hash`, so a reload, Back/Forward and a shared link all reopen
 * the list where it was.
 *
 *   #page=3&size=500&sort=name:asc&contract=<id>,<id>&attr=الشمري&case=<id>
 *
 * - Every field has a codec. A value that fails validation falls back to the
 *   field's default. A malformed hash never throws, it just yields defaults.
 * - Values equal to their default are left out, so the default list has an
 *   empty hash.
 * - Keys that no field declares are kept as they are when the hash is
 *   rewritten, so several consumers can share one hash.
 * - Changing a `history: 'push'` field (the default) adds a history entry.
 *   Changing only `history: 'replace'` fields (typing, a selected row)
 *   rewrites the current entry, debounced, so typing does not flood history.
 * - Text is percent-encoded as UTF-8, so Arabic survives copy/paste and
 *   reload. Commas and colons stay readable.
 *
 * Adopting it on a list (see `views/clients/list/hooks/useClientsHashState.ts`
 * for the full reference integration):
 *
 *   const schema = { page: { key: 'page', codec: hashCodec.int({ min: 1 }), default: 1 }, ... }
 *   const { state, present, write, setState } = useHashState(schema)
 *
 *   // URL -> screen: on mount (when `present`) and on every hash change,
 *   //                apply `state` to wherever the list keeps its state.
 *   // screen -> URL: when the list state changes, call `write(next)`.
 *
 * Define the schema at module level (or memoise it): it is a dependency of
 * the parsed state.
 */
import { useCallback, useEffect, useMemo, useRef } from "react";
import { useLocation, useNavigate } from "react-router-dom";

export type HashCodec<T> = {
  /** Returns `undefined` when `raw` is not a valid value. */
  parse: (raw: string) => T | undefined;
  /** The empty string means "leave the key out". */
  serialize: (value: T) => string;
};

export type HashField<T> = {
  /** Parameter name in the hash. Keep it short and stable: links get shared. */
  key: string;
  codec: HashCodec<T>;
  default: T;
  /** `push` (default) adds a history entry, `replace` rewrites the current one. */
  history?: "push" | "replace";
};

export type HashSchema<S> = { [K in keyof S]: HashField<S[K]> };

export type HashWriteOptions = {
  /** Force a history mode instead of deriving it from the changed fields. */
  replace?: boolean;
};

/** Refuse to parse absurdly long fragments (pasted garbage, not a list state). */
const MAX_HASH_LENGTH = 8192;

/* ------------------------------------------------------------------ */
/* Encoding                                                            */
/* ------------------------------------------------------------------ */

const safeDecode = (value: string): string | undefined => {
  try {
    return decodeURIComponent(value);
  } catch {
    return undefined;
  }
};

/** encodeURIComponent, but `,` and `:` (both legal in a fragment) stay readable. */
const encodeValue = (value: string) =>
  encodeURIComponent(value).replace(/%2C/gi, ",").replace(/%3A/gi, ":");

/** Parse `#a=1&b=2` into ordered [key, value] pairs. Never throws. */
export function decodeHashParams(hash: string): Array<[string, string]> {
  const body = (hash || "").replace(/^#/, "");
  if (!body || body.length > MAX_HASH_LENGTH) {
    return [];
  }
  const pairs: Array<[string, string]> = [];
  const seen = new Set<string>();
  for (const part of body.split("&")) {
    if (!part) continue;
    const eq = part.indexOf("=");
    const rawKey = eq === -1 ? part : part.slice(0, eq);
    const rawValue = eq === -1 ? "" : part.slice(eq + 1);
    const key = safeDecode(rawKey);
    const value = safeDecode(rawValue);
    // A malformed escape drops that one parameter, not the whole hash.
    if (!key || value === undefined || seen.has(key)) continue;
    seen.add(key);
    pairs.push([key, value]);
  }
  return pairs;
}

/** Build `#a=1&b=2` (or '' when there is nothing to write). */
export function encodeHashParams(pairs: Array<[string, string]>): string {
  const body = pairs
    .filter(([, value]) => value !== "")
    .map(([key, value]) => `${encodeValue(key)}=${encodeValue(value)}`)
    .join("&");
  return body ? `#${body}` : "";
}

/* ------------------------------------------------------------------ */
/* Codecs                                                              */
/* ------------------------------------------------------------------ */

const ID_PATTERN = /^[A-Za-z0-9_-]{1,64}$/;

/** Integer ids stay numbers, everything else (UUIDs) stays a string. */
const parseId = (raw: string): string | number | undefined => {
  if (!ID_PATTERN.test(raw)) return undefined;
  if (/^\d{1,15}$/.test(raw)) return Number(raw);
  return raw;
};

export const hashCodec = {
  /** Integer within [min, max]. */
  int({
    min = Number.MIN_SAFE_INTEGER,
    max = Number.MAX_SAFE_INTEGER,
  }: { min?: number; max?: number } = {}): HashCodec<number> {
    return {
      parse: (raw) => {
        if (!/^-?\d{1,15}$/.test(raw)) return undefined;
        const n = Number(raw);
        return Number.isSafeInteger(n) && n >= min && n <= max ? n : undefined;
      },
      serialize(value) {
        return this.parse(String(value)) === value ? String(value) : "";
      },
    };
  },

  /** One of a fixed set of values (e.g. page sizes, a status enum). */
  oneOf<T extends string | number>(values: readonly T[]): HashCodec<T> {
    return {
      parse: (raw) => values.find((v) => String(v) === raw),
      serialize: (value) =>
        values.some((v) => String(v) === String(value)) ? String(value) : "",
    };
  },

  /** Free text without control characters, capped in length. */
  text({ maxLength = 200 }: { maxLength?: number } = {}): HashCodec<
    string | undefined
  > {
    const parse = (raw: string) =>
      // eslint-disable-next-line no-control-regex
      raw && raw.length <= maxLength && !/[\u0000-\u001f\u007f]/.test(raw)
        ? raw
        : undefined;
    return {
      parse,
      serialize: (value) =>
        typeof value === "string" && parse(value) === value ? value : "",
    };
  },

  /** A calendar date, YYYY-MM-DD, that actually exists. */
  date(): HashCodec<string | undefined> {
    const parse = (raw: string) => {
      const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(raw);
      if (!m) return undefined;
      const d = new Date(Date.UTC(+m[1], +m[2] - 1, +m[3]));
      return d.getUTCFullYear() === +m[1] &&
        d.getUTCMonth() === +m[2] - 1 &&
        d.getUTCDate() === +m[3]
        ? raw
        : undefined;
    };
    return {
      parse,
      serialize: (value) =>
        typeof value === "string" && parse(value) === value ? value : "",
    };
  },

  /** A single record id (integer or UUID-like). */
  id(): HashCodec<string | number | undefined> {
    return {
      parse: parseId,
      serialize: (value) =>
        value === undefined || value === null || value === ""
          ? ""
          : ID_PATTERN.test(String(value))
          ? String(value)
          : "",
    };
  },

  /** Comma-separated ids. Invalid entries are dropped, not fatal. */
  idList({ maxItems = 200 }: { maxItems?: number } = {}): HashCodec<
    Array<string | number> | undefined
  > {
    return {
      parse: (raw) => {
        const ids = raw
          .split(",")
          .map(parseId)
          .filter((v): v is string | number => v !== undefined);
        return ids.length > 0 && ids.length <= maxItems ? ids : undefined;
      },
      // A list too long for a link, or with an id that would not parse
      // back, is left out rather than written half-way.
      serialize: (value) =>
        Array.isArray(value) &&
        value.length <= maxItems &&
        value.every((v) => ID_PATTERN.test(String(v)))
          ? value.map(String).join(",")
          : "",
    };
  },

  /** Table sort as `key:asc|desc`. Column keys are ASCII accessor paths. */
  sort(): HashCodec<{ key: string | number; order: "asc" | "desc" | "" }> {
    return {
      parse: (raw) => {
        const m = /^([A-Za-z0-9_.-]{0,80}):(asc|desc|)$/.exec(raw);
        if (!m || (!m[1] && !m[2])) return undefined;
        return { key: m[1], order: m[2] as "asc" | "desc" | "" };
      },
      serialize(value) {
        if (!value || (value.key === "" && value.order === "")) return "";
        const text = `${value.key ?? ""}:${value.order ?? ""}`;
        return this.parse(text) ? text : "";
      },
    };
  },
};

/* ------------------------------------------------------------------ */
/* Schema-level parse / serialise                                      */
/* ------------------------------------------------------------------ */

const fieldEntries = <S>(schema: HashSchema<S>) =>
  Object.keys(schema).map(
    (name) => [name, schema[name as keyof S]] as [keyof S, HashField<unknown>]
  );

/** Serialised value of one field, '' when it equals the default. */
const serializeField = (field: HashField<unknown>, value: unknown) => {
  const text = field.codec.serialize(value);
  return text === field.codec.serialize(field.default) ? "" : text;
};

export function parseHashState<S>(
  schema: HashSchema<S>,
  hash: string
): { state: S; present: boolean } {
  const params = new Map(decodeHashParams(hash));
  const state = {} as S;
  let present = false;
  for (const [name, field] of fieldEntries(schema)) {
    const raw = params.get(field.key);
    let value: unknown = field.default;
    if (raw !== undefined) {
      present = true;
      try {
        const parsed = field.codec.parse(raw);
        if (parsed !== undefined) value = parsed;
      } catch {
        // A codec bug must not take the screen down: use the default.
      }
    }
    state[name] = value as S[keyof S];
  }
  return { state, present };
}

/** `#…` for `state`, keeping any hash keys the schema does not own. */
export function serializeHashState<S>(
  schema: HashSchema<S>,
  state: S,
  currentHash = ""
): string {
  const owned = new Set(fieldEntries(schema).map(([, f]) => f.key));
  const pairs: Array<[string, string]> = fieldEntries(schema).map(
    ([name, field]) => [field.key, serializeField(field, state[name])]
  );
  for (const [key, value] of decodeHashParams(currentHash)) {
    if (!owned.has(key)) pairs.push([key, value]);
  }
  return encodeHashParams(pairs);
}

/** True when two states serialise the same (i.e. would produce the same URL). */
export function hashStatesEqual<S>(schema: HashSchema<S>, a: S, b: S): boolean {
  return fieldEntries(schema).every(
    ([name, field]) =>
      serializeField(field, a[name]) === serializeField(field, b[name])
  );
}

/* ------------------------------------------------------------------ */
/* Hook                                                                */
/* ------------------------------------------------------------------ */

export type UseHashStateOptions = {
  /** Debounce for replace writes that come from `history: 'replace'` fields
   *  (typing). Push writes and explicit `{ replace: true }` are immediate. */
  replaceDelayMs?: number;
};

/* There is one URL, so there is at most one queued (debounced) write for the
 * whole app, shared by every useHashState instance. A later write merges into
 * it instead of racing it. */
let queued: {
  timer: ReturnType<typeof setTimeout>;
  hash: string;
  pathname: string;
} | null = null;

/** `#` alone and no hash are the same URL state. */
const normalizeHash = (hash: string) => (hash === "#" ? "" : hash || "");

const cancelQueued = () => {
  if (queued) {
    clearTimeout(queued.timer);
    queued = null;
  }
};

function useHashState<S>(
  schema: HashSchema<S>,
  { replaceDelayMs = 300 }: UseHashStateOptions = {}
) {
  const location = useLocation();
  const navigate = useNavigate();

  const { state, present } = useMemo(
    () => parseHashState(schema, location.hash),
    [schema, location.hash]
  );

  // Writes always work from the newest location, not a stale closure.
  const locationRef = useRef(location);
  locationRef.current = location;

  /** The hash as it is now, or as it will be once a queued write lands. */
  const effectiveHash = useCallback(() => {
    const loc = locationRef.current;
    return queued && queued.pathname === loc.pathname ? queued.hash : loc.hash;
  }, []);

  // Back/Forward (or any outside navigation) wins over a queued write. Our
  // own writes clear the queue before they navigate, so a location change
  // that still finds a queued write came from somewhere else.
  const lastKey = useRef(location.key);
  useEffect(() => {
    if (lastKey.current !== location.key) {
      lastKey.current = location.key;
      cancelQueued();
    }
  }, [location.key]);

  // Leaving the screen drops a write that has not landed yet.
  useEffect(
    () => () => {
      if (queued && queued.pathname === locationRef.current.pathname) {
        cancelQueued();
      }
    },
    []
  );

  const write = useCallback(
    (next: S, options: HashWriteOptions = {}) => {
      const pathname = locationRef.current.pathname;
      const base = effectiveHash();
      const current = parseHashState(schema, base).state;

      const target = serializeHashState(schema, next, base);

      if (hashStatesEqual(schema, current, next)) {
        // Same state. An explicit replace still rewrites a URL that is not in
        // canonical form (invalid values, stray encodings), so a bad link
        // does not keep circulating.
        if (!options.replace || normalizeHash(base) === target) return;
      }

      let replace = options.replace;
      let debounce = false;
      if (replace === undefined) {
        const changed = fieldEntries(schema).filter(
          ([name, field]) =>
            serializeField(field, current[name]) !==
            serializeField(field, next[name])
        );
        replace = changed.every(([, f]) => f.history === "replace");
        debounce = replace && replaceDelayMs > 0;
      }

      const go = () => {
        queued = null;
        const now = locationRef.current;
        // The screen moved on (another route) before the write landed.
        if (now.pathname !== pathname) return;
        if (normalizeHash(now.hash) === target) return;
        navigate(
          { pathname: now.pathname, search: now.search, hash: target },
          { replace, state: now.state }
        );
      };

      cancelQueued();
      if (debounce) {
        queued = {
          timer: setTimeout(go, replaceDelayMs),
          hash: target,
          pathname,
        };
      } else {
        go();
      }
    },
    [schema, navigate, replaceDelayMs, effectiveHash]
  );

  /** The state as the next write will see it (includes a queued write). */
  const read = useCallback(
    () => parseHashState(schema, effectiveHash()).state,
    [schema, effectiveHash]
  );

  const setState = useCallback(
    (patch: Partial<S>, options?: HashWriteOptions) => {
      write({ ...read(), ...patch }, options);
    },
    [read, write]
  );

  return {
    /** State parsed from the current hash, validated, defaults filled in. */
    state,
    /** Whether the hash carried at least one of the schema's keys. */
    present,
    /** Raw `location.hash`; use it as an effect dependency for "hash changed". */
    hash: location.hash,
    /** Write a full state. No-op when the URL would not change. */
    write,
    /** Merge a partial state into the current state and write it. */
    setState,
    /** Current state including a queued write; for building the next write. */
    read,
  };
}

export default useHashState;
