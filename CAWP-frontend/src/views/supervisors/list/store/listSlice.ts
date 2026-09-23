import { createSlice, createAsyncThunk } from "@reduxjs/toolkit";
import {
  apiGetPortfolios,
  apPutPortfolio,
  apiDeletePortfolios,
  apiGetArchiveReasons,
} from "@/services/SuperviorsService";
import type { TableQueries } from "@/@types/common";

export type Portfolio = {
  count_clints: number;
  created_at: Date;
  id: string;
  is_active: boolean;
  name: string;
  total_amount: number;
  updated_at: Date;
};

type Filter = {
  customeFilterStatusesValue: boolean | number;
  customeFilterArchivingStatusesValue: boolean | number;
  customeFilterBranchesValue: string;
};

type GetPortfoliosResponse = {
  data: Portfolio[];
  total: number;
};

export type PortfoliosState = {
  loading: boolean;
  PortfolioList: Portfolio[];
  tableData: TableQueries;
  filterData: Filter;
  archiveReasons: Array;
};

export const SLICE_NAME = "supervisorsList";

export const getPortfolios = createAsyncThunk(
  "Supervisor/data/getSupervisor",
  async (data: TableQueries & { filterData?: Filter }) => {
    const response = await apiGetPortfolios<
      GetPortfoliosResponse,
      TableQueries
    >(data);
    return response.data;
  }
);
export const getArchiveReasons = createAsyncThunk(
  "Supervisor/data/getArchiveReasons",
  async () => {
    const response = await apiGetArchiveReasons<branch, {}>({});
    return response.data;
  }
);

export const deletePortfolios = async <T, U extends Record<string, unknown>>(
  data: U
) => {
  const response = await apiDeletePortfolios<T, U>(data);
  return response.data;
};

export const putPortfoliolsToArchive = async <
  T,
  U extends Record<string, unknown>
>(
  data: U
) => {
  const response = await apPutPortfolio<T, U>(data);
  return response.data;
};

export const initialTableData: TableQueries = {
  total: 0,
  page: 1,
  pageSize: 100,
  query: "",
  sort: {
    order: "",
    key: "",
  },
};

export const initialFilterData = {
  customeFilterStatusesValue: 0,
  customeFilterArchivingStatusesValue: 0,
  customeFilterBranchesValue: "",
};

const initialState: PortfoliosState = {
  loading: false,
  PortfolioList: [],
  tableData: initialTableData,
  filterData: initialFilterData,
  archiveReasons: [],
};

const listSlice = createSlice({
  name: `${SLICE_NAME}/state`,
  initialState,
  reducers: {
    setTableData: (state, action) => {
      state.tableData = action.payload;
    },
    setPortfolioList: (state, action) => {
      state.PortfolioList = action.payload;
    },
    setFilterData: (state, action) => {
      state.filterData = action.payload;
    },
  },
  extraReducers: (builder) => {
    builder
      .addCase(getPortfolios.fulfilled, (state, action) => {
        state.list = action.payload.supervisors;
        state.tableData.total = action.payload.supervisors.length;
        state.loading = false;
      })

      .addCase(getPortfolios.pending, (state) => {
        state.loading = true;
      })
      .addCase(getPortfolios.rejected, (state) => {
        state.loading = false;
      })
      .addCase(getArchiveReasons.fulfilled, (state, action) => {
        state.archiveReasons = action.payload.reson_archive;
      });
  },
});

export const { setTableData, setPortfolioList, setFilterData } =
  listSlice.actions;

export default listSlice.reducer;
