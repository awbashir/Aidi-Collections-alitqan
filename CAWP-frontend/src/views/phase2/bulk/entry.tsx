/**
 * Entry points into the reviewed bulk wizard for the existing screens.
 * Every item is hidden unless `GET /v2/bulk/kinds` says the caller may use
 * that kind (`allowed`), so a button never leads to a 403.
 */
import { useTranslation } from 'react-i18next'
import { HiOutlineCollection } from 'react-icons/hi'
import Button from '@/components/ui/Button'
import Dropdown from '@/components/ui/Dropdown'
import { Fragment } from 'react'
import type { ReactNode } from 'react'
import { useBulkKinds, useLaunchBulk } from './shared'
import type { BulkLaunch } from './shared'

export type BulkEntry = {
    kind: string
    launch: BulkLaunch
    /** Default: `p2.entry.<kind>`. */
    label?: ReactNode
    testId?: string
    disabled?: boolean
}

export type BulkEntryGroup = { header?: ReactNode; entries: BulkEntry[] }

/** "Reviewed bulk actions" dropdown for a list screen. Renders nothing when no entry is allowed. */
export const BulkMenu = ({
    groups,
    title,
    testId = 'p2-bulk-menu',
    compact,
}: {
    groups: BulkEntryGroup[]
    title?: ReactNode
    testId?: string
    /** A small toggle for row actions. The toggle is always a secondary button: each screen keeps its one primary. */
    compact?: boolean
}) => {
    const { t } = useTranslation()
    const kinds = useBulkKinds()
    const launch = useLaunchBulk()
    const visible = groups
        .map((g) => ({ ...g, entries: g.entries.filter((e) => kinds.allowed(e.kind)) }))
        .filter((g) => g.entries.length > 0)
    if (visible.length === 0) return null
    return (
        <span data-testid={testId}>
            <Dropdown
                placement="bottom-end"
                renderTitle={
                    <Button variant="default" size={compact ? 'xs' : 'sm'} icon={<HiOutlineCollection />} data-testid={`${testId}-toggle`}>
                        {title || t('p2.entry.menu')}
                    </Button>
                }
            >
                {visible.map((g, gi) => (
                    <Fragment key={gi}>
                        {gi > 0 && <Dropdown.Item variant="divider" />}
                        {g.header && <Dropdown.Item variant="header">{g.header}</Dropdown.Item>}
                        {g.entries.map((e) => (
                            <Dropdown.Item
                                key={`${e.kind}:${e.testId || ''}`}
                                eventKey={`${e.kind}:${e.testId || ''}`}
                                disabled={e.disabled}
                                onClick={() => !e.disabled && launch(e.kind, e.launch)}
                            >
                                <span data-testid={e.testId} data-kind={e.kind}>
                                    {e.label || t(`p2.entry.${e.kind}`)}
                                </span>
                            </Dropdown.Item>
                        ))}
                    </Fragment>
                ))}
            </Dropdown>
        </span>
    )
}

/** A single button that opens the wizard for one kind; hidden unless allowed. */
export const BulkLaunchButton = ({
    kind,
    launch,
    label,
    testId,
    size = 'sm',
    variant = 'default',
    icon,
}: {
    kind: string
    launch: BulkLaunch
    label: ReactNode
    testId?: string
    size?: 'xs' | 'sm'
    variant?: 'default' | 'solid' | 'plain'
    icon?: ReactNode
}) => {
    const kinds = useBulkKinds()
    const go = useLaunchBulk()
    if (!kinds.allowed(kind)) return null
    return (
        <Button size={size} variant={variant} icon={icon} onClick={() => go(kind, launch)} data-testid={testId} data-kind={kind}>
            {label}
        </Button>
    )
}
