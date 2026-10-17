import { useMemo } from "react";
import MyFilter from "@/components/shared/myComponents/filter";
import { subStatusesOf, StatusLike } from "@/utils/commentStatusTree";

/**
 * Sub-status picker of the report and list filters (migration 0068): lists the
 * live sub-statuses of the main status(es) chosen in the status picker next to
 * it, so it is empty until a main status is chosen. When the main choice
 * changes its options change, and MyFilter then clears the sub-status choice.
 *
 * `statuses` are the raw `/comment_status` rows (main and sub-statuses);
 * `selectedMain` is what the main picker reports (rows or ids).
 */
type Status = StatusLike & { id?: string; name?: string };

export function subStatusesOfMains<T extends StatusLike>(
  statuses: T[] | null | undefined,
  mains: Array<{ id?: string } | string> | null | undefined
): T[] {
  const ids = (Array.isArray(mains) ? mains : [])
    .map((m) => (typeof m === "string" ? m : m?.id))
    .filter((id): id is string => !!id);
  return ids.flatMap((id) => subStatusesOf(statuses, id));
}

type Props = {
  statuses: Status[] | null | undefined;
  selectedMain: Array<{ id?: string } | string> | null | undefined;
  filter: (values: Status[]) => void;
  selectedIds?: string[];
  isMulti?: boolean;
};

const SubStatusFilter = ({
  statuses,
  selectedMain,
  filter,
  selectedIds,
  isMulti = true,
}: Props) => {
  const mainKey = (Array.isArray(selectedMain) ? selectedMain : [])
    .map((m) => (typeof m === "string" ? m : m?.id))
    .join(",");
  const options = useMemo(
    () => subStatusesOfMains(statuses, selectedMain),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [statuses, mainKey]
  );
  return (
    <MyFilter
      options={options}
      filter={filter}
      selectedIds={selectedIds}
      labelField={{
        plural: "p8.reports.subStatuses",
        singular: "p8.reports.subStatus",
        title: "p8.reports.subStatus",
      }}
      isMulti={isMulti}
    />
  );
};

export default SubStatusFilter;
