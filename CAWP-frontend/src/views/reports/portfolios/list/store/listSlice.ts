import { createSlice, createAsyncThunk } from "@reduxjs/toolkit";
import {
  apPutPortfolio,
  apiDeletePortfolios,
  apiGetArchiveReasons,
  apiGetCollectionMethods,
  apiGetCollectionTypes,
  apiGetUsers,
  apiGetPaymentDetails,
  apiEditPayment,
  apiPutRejectPayment,
  apiGetPortfolios,
} from "@/services/ReportsService";
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
  collectionMethods: Array;
  collectionTypes: Array;
  users: Array;
  paymentDetails: object;
  search: boolean;
};

export const SLICE_NAME = "inactiveClientsList";

export const getPortfolios = createAsyncThunk(
  "Portfolios/data/getPortfolios",
  async (data: TableQueries & { filterData?: Filter }) => {
    const response = await apiGetPortfolios<
      GetPortfoliosResponse,
      TableQueries
    >(data);
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

export const getCollectionMethods = createAsyncThunk(
  "Portfolios/data/getCollectionMethods",
  async () => {
    const response = await apiGetCollectionMethods<branch, {}>({});
    return response.data;
  }
);

export const getCollectionTypes = createAsyncThunk(
  "Portfolios/data/getCollectionTypes",
  async () => {
    const response = await apiGetCollectionTypes<branch, {}>({});
    return response.data;
  }
);

export const getUsers = createAsyncThunk(
  "Portfolios/data/getUsers",
  async () => {
    const response = await apiGetUsers<branch, {}>({});
    return response.data;
  }
);

export const getPaymentDetails = createAsyncThunk(
  "Portfolios/data/getPaymentDetails",
  async (data: { id: string }) => {
    const response = await apiGetPaymentDetails<branch, { id: string }>(data);
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

export const putPayment = async <T, U extends Record<string, unknown>>(
  data: U
) => {
  const response = await apiEditPayment<T, U>(data);
  return response.data;
};

export const putRejectPayment = async <T, U extends Record<string, unknown>>(
  data: U
) => {
  const response = await apiPutRejectPayment<T, U>(data);
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

function formatDate(date) {
  let d = new Date(date),
    month = "" + (d.getMonth() + 1),
    day = "" + d.getDate(),
    year = d.getFullYear();

  if (month.length < 2) month = "0" + month;
  if (day.length < 2) day = "0" + day;

  return [year, month, day].join("-");
}
const current = new Date(); // get current date
const firstDayOfMonth = new Date(current.getFullYear(), current.getMonth(), 1);
const lastDayOfMonth = new Date(
  current.getFullYear(),
  current.getMonth() + 1,
  0
);
const selectedMoveDate = [
  formatDate(firstDayOfMonth),
  formatDate(lastDayOfMonth),
];

export const initialFilterData = {
  customeFilterBranchesValue2: [],
  customeFilterDateValue: selectedMoveDate,
};

const initialState: PortfoliosState = {
  loading: false,
  PortfolioList: [],
  tableData: initialTableData,
  filterData: initialFilterData,
  archiveReasons: [],
  collectionMethods: [],
  collectionTypes: [],
  users: [],
  paymentDetails: {},
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
        state.list = action.payload.portfolios;
        state.tableData.total = action.payload.countRow;
        state.filterData;
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
      })
      .addCase(getCollectionMethods.fulfilled, (state, action) => {
        state.collectionMethods = action.payload.collection_methods;
      })
      .addCase(getCollectionTypes.fulfilled, (state, action) => {
        state.collectionTypes = action.payload.collection_types;
      })
      .addCase(getUsers.fulfilled, (state, action) => {
        state.users = action.payload.users;
      })
      .addCase(getPaymentDetails.fulfilled, (state, action) => {
        const { payment_date } = action.payload;
        const date = new Date(payment_date);
        const year = date.getFullYear();
        const month = date.getMonth() + 1;
        const dt = date.getDate();

        // Ensure month and date are 2-digits
        const formattedMonth = month < 10 ? `0${month}` : `${month}`;
        const formattedDate = dt < 10 ? `0${dt}` : `${dt}`;

        action.payload.payment_date = `${year}-${formattedMonth}-${formattedDate}`;
        state.paymentDetails = action.payload;
      })
      .addCase(getPaymentDetails.pending, (state) => {
        state.paymentDetails = {};
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
