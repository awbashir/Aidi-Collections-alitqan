import { createSlice, createAsyncThunk } from "@reduxjs/toolkit";
import {
  apiGetClients2,
  apPutClient,
  apiDeleteClients,
  apiDeleteClintsByIDs,
  apiGetArchiveReasons,
  apiGetBranches,
  apiGetUsers,
  apiGetContracts,
  apiGetUpdateSides,
} from "@/services/ClientsService";
import type { TableQueries } from "@/@types/common";

export type Client = {
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

type GetClientsResponse = {
  data: Client[];
  total: number;
};

export type ClientsState = {
  loading: boolean;
  ClientList: Client[];
  tableData: TableQueries;
  filterData: Filter;
  archiveReasons: Array;
  branches: Array;
  contracts: Array;
  users: Array;
  updateSides: Array;
  search: boolean;
  selectedRow: Array;
};

export const SLICE_NAME = "clientsList2";

export const getClients = createAsyncThunk(
  "Clients/data/2/getClients",
  async (data: TableQueries & { filterData?: Filter }) => {
    const response = await apiGetClients2<GetClientsResponse, TableQueries>(
      data
    );
    return response.data;
  }
);
export const getArchiveReasons = createAsyncThunk(
  "Clients/data/2/getArchiveReasons",
  async () => {
    const response = await apiGetArchiveReasons<branch, {}>({});
    return response.data;
  }
);

export const getBranches = createAsyncThunk(
  "Clients/data/2/getBranches",
  async () => {
    const response = await apiGetBranches<branch, {}>({});
    return response.data;
  }
);

export const getUsers = createAsyncThunk("Clients/data/2/getUsers", async () => {
  const response = await apiGetUsers<branch, {}>({});
  return response.data;
});

export const getContracts = createAsyncThunk(
  "Clients/data/2/getContracts",
  async () => {
    const response = await apiGetContracts<branch, {}>({});
    return response.data;
  }
);

export const getUpdateSides = createAsyncThunk(
  "Clients/data/2/getUpdateSides",
  async () => {
    const response = await apiGetUpdateSides<branch, {}>({});
    return response.data;
  }
);

export const deleteClients = async <T, U extends Record<string, unknown>>(
  data: U
) => {
  const response = await apiDeleteClients<T, U>(data);
  return response.data;
};

export const deleteClintsByIDs = async <T, U extends Record<string, unknown>>(
  data: U
) => {
  const response = await apiDeleteClintsByIDs<T, U>(data);
  return response.data;
};

export const putClientlsToArchive = async <
  T,
  U extends Record<string, unknown>
>(
  data: U
) => {
  const response = await apPutClient<T, U>(data);
  return response.data;
};

export const initialTableData: TableQueries = {
  total: 0,
  page: 1,
  pageSize: 100,
  totalAmount: 0,
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
  customeFilterInquiryClintByExcelValue: [],
};

const initialState: ClientsState = {
  loading: false,
  ClientList: [],
  tableData: initialTableData,
  filterData: initialFilterData,
  archiveReasons: [],
  branches: [],
  users: [],
  contracts: [],
  updateSides: [],
  search: false,
  selectedRow: [],
};

const listSlice = createSlice({
  name: `${SLICE_NAME}/state`,
  initialState,
  reducers: {
    setTableData: (state, action) => {
      state.tableData = action.payload;
    },
    setClientList: (state, action) => {
      state.ClientList = action.payload;
    },
    setFilterData: (state, action) => {
      state.filterData = action.payload;
    },
    setSearchOpen: (state) => {
      state.search = !state.search;
    },
    setSearchClose: (state) => {
      state.search = false;
    },
    setSelectedRow: (state, action) => {
      state.selectedRow = action.payload;
    },
  },
  extraReducers: (builder) => {
    builder
      .addCase(getClients.fulfilled, (state, action) => {
        state.list = action.payload.clints;
        state.tableData.total = action.payload.countRow;
        state.tableData.totalAmount = action.payload.totalAmount;
        state.loading = false;
      })

      .addCase(getClients.pending, (state) => {
        state.loading = true;
        state.selectedRow = [];
      })
      .addCase(getClients.rejected, (state) => {
        state.loading = false;
      })
      .addCase(getArchiveReasons.fulfilled, (state, action) => {
        state.archiveReasons = action.payload.reson_archive;
      })
      .addCase(getBranches.fulfilled, (state, action) => {
        state.branches = action.payload.branches;
      })
      .addCase(getUsers.fulfilled, (state, action) => {
        state.users = action.payload.users;
      })
      .addCase(getContracts.fulfilled, (state, action) => {
        state.contracts = action.payload.contracts;
      })
      .addCase(getUpdateSides.fulfilled, (state, action) => {
        state.updateSides = action.payload.updateSides;
      });
  },
});

export const {
  setTableData,
  setClientList,
  setFilterData,
  setSearchClose,
  setSearchOpen,
  setSelectedRow,
} = listSlice.actions;

export default listSlice.reducer;
