/**
 * /archive keeps page, page size, sort, query and its search filters in the
 * URL hash (like /clients), so a reload or Back reopens the same list. The
 * spreadsheet inquiry list stays out of the hash.
 */
import useListHashSync, {
  listHashBaseSchema,
  type ListHashBase,
} from "@/utils/hooks/useListHashSync";
import { hashCodec, type HashSchema } from "@/utils/hooks/useHashState";
import {
  initialTableData,
  setFilterData,
  setTableData,
  useAppSelector,
} from "../store";

type Ids = Array<string | number> | undefined;

type ArchiveFilters = {
  contract: Ids;
  portfolio: Ids;
  status: Ids;
  subStatus: Ids;
  user: Ids;
  reason: Ids;
  by: Ids;
  account: string | undefined;
};

const ids = (key: string) => ({
  key,
  codec: hashCodec.idList(),
  default: undefined as Ids,
});

const schema: HashSchema<ListHashBase & ArchiveFilters> = {
  ...listHashBaseSchema([100, 500, 1000], initialTableData.pageSize as number),
  contract: ids("contract"),
  portfolio: ids("portfolio"),
  status: ids("status"),
  subStatus: ids("substatus"),
  user: ids("user"),
  reason: ids("reason"),
  by: ids("by"),
  account: {
    key: "account",
    codec: hashCodec.text({ maxLength: 80 }),
    default: undefined,
  },
};

const filterKeys = {
  contract: "selectedContracts_Qkey",
  portfolio: "SelectedPortfolios_Qkey",
  status: "SelectedCommentStatus_Qkey",
  subStatus: "SelectedCommentSubStatus_Qkey",
  user: "selectedUsers_Qkey",
  reason: "SelectedResons_Qkey",
  by: "selectedArchiveBy_Qkey",
  account: "selectedAccountNumber_Qkey",
};

/** Returns `ready`: render the list only when it is true. */
export function useArchiveHashSync(): boolean {
  const tableData = useAppSelector((state) => state.archiveList.data.tableData);
  const filterData = useAppSelector(
    (state) => state.archiveList.data.filterData
  );
  return useListHashSync<ArchiveFilters>({
    schema,
    filterKeys,
    tableData,
    filterData,
    setTableData,
    setFilterData,
  });
}
