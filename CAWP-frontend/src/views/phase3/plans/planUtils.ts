/**
 * Non-component helpers for the repayment-plan screens. Nothing here does
 * money arithmetic: amounts stay the API's decimal strings.
 */
import { describeP3Error } from '@/services/v2'
import type { Translate } from '@/services/v2'

/** The case detail screen's plan tab (`/clients/details/plan?id=`). */
export const casePlanLink = (caseId: string) => `/clients/details/plan?id=${encodeURIComponent(caseId)}`

/** Error sentences with the plan wording for shared codes. */
export const describePlanError = (err: unknown, t: Translate) => describeP3Error(err, t, 'plan')

/** A positive 2-decimal string (> 0), checked as text. */
export const isPositiveAmount = (value: string | null | undefined) =>
    !!value && !/^\s*-/.test(value) && !/^[+]?0*(\.0*)?$/.test(value.trim())

/** "4ee2a1b0-…" -> "4ee2a1b0" for display (the full id sits in the title). */
export const shortId = (id: string | null | undefined) => (id ? id.slice(0, 8) : '—')
