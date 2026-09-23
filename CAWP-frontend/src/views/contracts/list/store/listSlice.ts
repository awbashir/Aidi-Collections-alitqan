import { createSlice, createAsyncThunk } from "@reduxjs/toolkit";
import {
  apiGetContracts,
  apPutContract,
  apiPutDeactivateContract,
  apiPutActivateContract,
} from "@/services/ContractsService";
import type { TableQueries } from "@/@types/common";

export type Contract = {
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

type GetContractsResponse = {
  data: Contract[];
  total: number;
};

export type ContractsState = {
  loading: boolean;
  ContractList: Contract[];
  tableData: TableQueries;
  filterData: Filter;
};

export const SLICE_NAME = "contractsList";

export const getContracts = createAsyncThunk(
  "Contracts/data/getContracts",
  async (data: TableQueries & { filterData?: Filter }) => {
    const response = await apiGetContracts<GetContractsResponse, TableQueries>(
      data
    );
    return response.data;
  }
);

export const activateContract = async <T, U extends Record<string, unknown>>(
  data: U
) => {
  const response = await apiPutActivateContract<T, U>(data);
  return response.data;
};

export const deactivateContract = async <T, U extends Record<string, unknown>>(
  data: U
) => {
  const response = await apiPutDeactivateContract<T, U>(data);
  return response.data;
};

export const initialTableData: TableQueries = {
  total: 0,
  page: 1,
  pageSize: 10,
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

const initialState: ContractsState = {
  loading: false,
  ContractList: [],
  tableData: initialTableData,
  filterData: initialFilterData,
};

const listSlice = createSlice({
  name: `${SLICE_NAME}/state`,
  initialState,
  reducers: {
    setTableData: (state, action) => {
      state.tableData = action.payload;
    },
    setContractList: (state, action) => {
      state.ContractList = action.payload;
    },
    setFilterData: (state, action) => {
      state.filterData = action.payload;
    },
  },
  extraReducers: (builder) => {
    builder
      .addCase(getContracts.fulfilled, (state, action) => {
        state.list = action.payload.contracts;
        state.tableData.total = action.payload.countRow;
        state.loading = false;
      })

      .addCase(getContracts.pending, (state) => {
        state.loading = true;
      });
  },
});

export const { setTableData, setContractList, setFilterData } =
  listSlice.actions;

export default listSlice.reducer;
