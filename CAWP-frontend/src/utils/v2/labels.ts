/** Labels shared by several screens (kept out of component files for fast refresh). */
import type { AttentionReason } from '@/services/v2/types'

/** An attention reason's label: Phase 1 reasons under `p1.reason.*`, Phase 3 ones under `p3.attention.reason.*`. */
export const reasonLabel = (t: (k: string, o?: Record<string, unknown>) => string, reason: AttentionReason) =>
    t(`p1.reason.${reason}`, { defaultValue: t(`p3.attention.reason.${reason}`) })
