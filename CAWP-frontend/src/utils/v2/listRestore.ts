/**
 * Helpers for list filter panels that start from the filters the list already
 * applies (kept in its Redux slice and, for some lists, the URL hash), so a
 * panel rebuilt after coming back from a case shows what is applied instead
 * of its defaults.
 */

/** Ids applied to a list -> the `{ id }` shape the legacy selects report
 *  (typed loosely, like the untyped option objects it stands in for). */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export const asSelection = (ids: unknown): Array<{ id: any }> | undefined =>
    Array.isArray(ids) && ids.length > 0 ? ids.map((id) => ({ id })) : undefined

/** A select's value -> its ids (for `MyFilter`'s `selectedIds`). */
export const selectionIds = (list: unknown): unknown[] | undefined =>
    Array.isArray(list) ? list.map((item) => (item as { id?: unknown })?.id) : undefined

/** A stable key for "which ids are selected" (identity changes ignored). */
export const selectionKey = (list: unknown): string =>
    Array.isArray(list) ? list.map((item) => String((item as { id?: unknown })?.id)).join(',') : ''

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/

/** `[from, to]` as applied (YYYY-MM-DD each), or null when it is not one. */
export function appliedDateRange(value: unknown): [string, string] | null {
    return Array.isArray(value) &&
        value.length === 2 &&
        value.every((v) => typeof v === 'string' && ISO_DATE.test(v))
        ? [value[0], value[1]]
        : null
}

/** YYYY-MM-DD -> a local Date on that calendar day (no UTC shift). */
export function localDate(value: string): Date {
    const [y, m, d] = value.split('-').map(Number)
    return new Date(y, m - 1, d)
}
