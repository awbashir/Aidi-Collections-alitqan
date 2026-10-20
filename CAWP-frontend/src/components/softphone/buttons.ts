/** Softphone button classes (shared by the panel's tabs, the line card and call control). */
const btn =
    'inline-flex h-9 items-center justify-center gap-1.5 rounded-md border px-3 text-[13px] font-extrabold transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-azure-500 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50'
export const btnSecondary = `${btn} border-slate-300 bg-white text-navy-600 hover:bg-slate-50 dark:border-night-border dark:bg-night-card dark:text-night-strong`
export const btnPrimary = `${btn} border-navy-600 bg-navy-600 text-white hover:bg-ink-800 active:bg-ink-950`
export const btnDanger = `${btn} border-danger bg-danger text-white hover:opacity-90`
