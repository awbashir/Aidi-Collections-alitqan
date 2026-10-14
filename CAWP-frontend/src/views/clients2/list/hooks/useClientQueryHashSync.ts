/**
 * /client_query keeps page, page size, sort, query and its search fields in
 * the URL hash (like /clients), so a reload or Back reopens the same lookup.
 * The spreadsheet inquiry list stays out of the hash.
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

type ClientQueryFilters = {
  mobile: string | undefined;
  account: string | undefined;
  attr: string | undefined;
};

const schema: HashSchema<ListHashBase & ClientQueryFilters> = {
  ...listHashBaseSchema([100, 500, 1000], initialTableData.pageSize as number),
  mobile: {
    key: "mobile",
    codec: hashCodec.text({ maxLength: 50 }),
    default: undefined,
  },
  account: {
    key: "account",
    codec: hashCodec.text({ maxLength: 80 }),
    default: undefined,
  },
  attr: {
    key: "attr",
    codec: hashCodec.text({ maxLength: 200 }),
    default: undefined,
  },
};

const filterKeys = {
  mobile: "customeFilterMobileNumberValue",
  // (sic) the key the API reads.
  account: "customeFilrerAccountNumberValue",
  attr: "customeFilterAttributeValue",
};

/** Returns `ready`: render the list only when it is true. */
export function useClientQueryHashSync(): boolean {
  const tableData = useAppSelector(
    (state) => state.clientsList2.data.tableData
  );
  const filterData = useAppSelector(
    (state) => state.clientsList2.data.filterData
  );
  return useListHashSync<ClientQueryFilters>({
    schema,
    filterKeys,
    tableData,
    filterData,
    setTableData,
    setFilterData,
  });
}
