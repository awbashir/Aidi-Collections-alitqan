import { createSlice, createAsyncThunk } from "@reduxjs/toolkit";
import {
  apiGetArchive,
  apPutPortfolio,
  apiDeletePortfolios,
  apiGetArchiveReasons,
  apiUnarchive,
} from "@/services/ArchiveService";
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

type Filter = {};

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
  search: boolean;
};

export const SLICE_NAME = "archiveList";

export const getPortfolios = createAsyncThunk(
  "Portfolios/data/getPortfolios",
  async (data: TableQueries & { filterData?: Filter }) => {
    const response = await apiGetArchive<GetPortfoliosResponse, TableQueries>(
      data
    );
    return response.data;
  }
);
export const getArchiveReasons = createAsyncThunk(
  "Portfolios/data/getArchiveReasons",
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

export const putUnarchivePortfolios = async <
  T,
  U extends Record<string, unknown>
>(
  data: U
) => {
  const response = await apiUnarchive<T, U>(data);
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

export const initialFilterData = {};

const initialState: PortfoliosState = {
  loading: false,
  PortfolioList: [],
  tableData: initialTableData,
  filterData: initialFilterData,
  archiveReasons: [],
  search: false,
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
    setSearchOpen: (state) => {
      state.search = true;
    },
    setSearchClose: (state) => {
      state.search = false;
    },
  },
  extraReducers: (builder) => {
    builder
      .addCase(getPortfolios.fulfilled, (state, action) => {
        state.list = action.payload.clints_archive;
        state.tableData.total = action.payload.countRow;
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

export const {
  setTableData,
  setPortfolioList,
  setFilterData,
  setSearchClose,
  setSearchOpen,
} = listSlice.actions;

export default listSlice.reducer;
