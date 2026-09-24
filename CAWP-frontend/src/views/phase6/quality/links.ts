/** Constants and links of the quality workspace (no components here, so the component files keep fast refresh). */

/** The sections of one recording (the open one is in the URL hash, `#rec=<id>&sec=<section>`). */
export type Section = 'evidence' | 'transcript' | 'review' | 'approvals'
export const SECTIONS: Section[] = ['evidence', 'transcript', 'review', 'approvals']

/** A link to one recording in the quality workspace. */
export const qualityLink = (recordingId: string) => `/quality#rec=${encodeURIComponent(recordingId)}`
