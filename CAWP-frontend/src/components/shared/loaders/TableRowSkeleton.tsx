import Skeleton from '@/components/ui/Skeleton'
import Table from '@/components/ui/Table'
import type { SkeletonProps } from '@/components/ui/Skeleton'

type TableRowSkeletonProps = {
    columns?: number
    rows?: number
    avatarInColumns?: number[]
    avatarProps?: SkeletonProps
}

const { Tr, Td, TBody } = Table

/**
 * Loading a table is a skeleton at the real row height in `#F2F6F8`, never a
 * spinner — the layout must not jump when the data arrives.
 */
const TableRowSkeleton = (props: TableRowSkeletonProps) => {
    const { columns = 1, rows = 10, avatarInColumns = [], avatarProps } = props

    return (
        <TBody>
            {Array.from(new Array(rows), (_, i) => i + 0).map((row) => (
                <Tr key={`row-${row}`}>
                    {Array.from(new Array(columns), (_, i) => i + 0).map(
                        (col) => (
                            <Td key={`col-${col}`}>
                                <div className="flex flex-auto items-center gap-2">
                                    {avatarInColumns.includes(col) && (
                                        <div>
                                            <Skeleton
                                                variant="circle"
                                                className="!bg-gray-100 dark:!bg-night-raised"
                                                {...avatarProps}
                                            />
                                        </div>
                                    )}
                                    <Skeleton
                                        height={10}
                                        className="!bg-gray-100 dark:!bg-night-raised"
                                    />
                                </div>
                            </Td>
                        )
                    )}
                </Tr>
            ))}
        </TBody>
    )
}

export default TableRowSkeleton
