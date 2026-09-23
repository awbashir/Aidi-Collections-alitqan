import { createSlice, createAsyncThunk } from "@reduxjs/toolkit";
import {
  apiGetCrmUsers,
  apPutCrmUser,
  apiGetCrmUsersStatistic,
  apiGetBranches,
  apiPutActivateUser,
  apiPutDeactivateUser,
} from "@/services/UserService";
import type { TableQueries } from "@/@types/common";

type branch = {
  id: string;
  is_active: boolean;
  name: string;
  created_at: Date;
  updated_at: Date;
};

type nationality = {
  id: string;
  is_active: boolean;
  name: string;
  created_at: Date;
  updated_at: Date;
};

type role = {
  id: string;
  is_active: boolean;
  name: string;
  created_at: Date;
  updated_at: Date;
  permission: JSON;
};

type supervisoruser = User;

type PersonalInfo = {
  location: string;
  title: string;
  birthday: string;
  phoneNumber: string;
  facebook: string;
  twitter: string;
  pinterest: string;
  linkedIn: string;
};

type OrderHistory = {
  id: string;
  item: string;
  status: string;
  amount: number;
  date: number;
};

type PaymentMethod = {
  cardHolderName: string;
  cardType: string;
  expMonth: string;
  expYear: string;
  last4Number: string;
  primary: boolean;
};

type Subscription = {
  plan: string;
  status: string;
  billing: string;
  nextPaymentDate: number;
  amount: number;
};

export type User = {
  branch: branch;
  branch_id: string;
  count_clints: number;
  created_at: Date;
  email: string;
  frist_name: string;
  id: string;
  identityNumber: string;
  is_active: boolean;
  last_name: string;
  mobile: string;
  name: string;
  nationality: nationality;
  nationality_id: string;
  password: string;
  role: role;
  role_id: string;
  supervisor: supervisoruser[];
  total_amount: number;
  updated_at: Date;
  user_name: string;

  img: string;
  role: string;
  lastOnline: number;
  status: string;
  personalInfo: PersonalInfo;
  orderHistory: OrderHistory[];
  paymentMethod: PaymentMethod[];
  subscription: Subscription[];
};

type Statistic = {
  value: number;
  growShrink: number;
};

type UserStatistic = {
  totalUsers: Statistic;
  activeUsers: Statistic;
  newUsers: Statistic;
};

type Filter = {
  customeFilterStatusesValue: boolean | number;
  customeFilterArchivingStatusesValue: boolean | number;
  customeFilterBranchesValue: string;
};

type GetCrmUsersResponse = {
  data: User[];
  total: number;
};

type GetCrmUsersStatisticResponse = UserStatistic;

export type UsersState = {
  loading: boolean;
  statisticLoading: boolean;
  userList: User[];
  branchesList?: branch[];
  statisticData: Partial<UserStatistic>;
  tableData: TableQueries;
  filterData: Filter;
  drawerOpen: boolean;
  selectedUser: Partial<User>;
};

export const SLICE_NAME = "crmUsers";

export const getUserStatistic = createAsyncThunk(
  "crmUsers/data/getUserStatistic",
  async () => {
    const response =
      await apiGetCrmUsersStatistic<GetCrmUsersStatisticResponse>();
    return response.data;
  }
);

export const getUsers = createAsyncThunk(
  "crmUsers/data/getUsers",
  async (data: TableQueries & { filterData?: Filter }) => {
    const response = await apiGetCrmUsers<GetCrmUsersResponse, TableQueries>(
      data
    );
    return response.data;
  }
);

export const getBranches = createAsyncThunk(
  "crmUsers/data/getBranches",
  async () => {
    const response = await apiGetBranches<branch, {}>({});
    return response.data;
  }
);

export const putUser = createAsyncThunk(
  "crmUsers/data/putUser",
  async (data: User) => {
    const response = await apPutCrmUser(data);
    return response.data.nationalities;
  }
);

export const activateUser = async <T, U extends Record<string, unknown>>(
  data: U
) => {
  const response = await apiPutActivateUser<T, U>(data);
  return response.data;
};

export const deactivateUser = async <T, U extends Record<string, unknown>>(
  data: U
) => {
  const response = await apiPutDeactivateUser<T, U>(data);
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

const initialState: UsersState = {
  loading: false,
  statisticLoading: false,
  userList: [],
  branchesList: [],
  statisticData: {},
  tableData: initialTableData,
  filterData: initialFilterData,
  drawerOpen: false,
  selectedUser: {},
};

const usersSlice = createSlice({
  name: `${SLICE_NAME}/state`,
  initialState,
  reducers: {
    setTableData: (state, action) => {
      state.tableData = action.payload;
    },
    setUserList: (state, action) => {
      state.userList = action.payload;
    },
    setFilterData: (state, action) => {
      state.filterData = action.payload;
    },
    setSelectedUser: (state, action) => {
      state.selectedUser = action.payload;
    },
    setDrawerOpen: (state) => {
      state.drawerOpen = true;
    },
    setDrawerClose: (state) => {
      state.drawerOpen = false;
    },
  },
  extraReducers: (builder) => {
    builder
      .addCase(getUsers.fulfilled, (state, action) => {
        state.userList = action.payload.users;
        state.tableData.total = action.payload.countRow;
        state.loading = false;
      })
      .addCase(getBranches.fulfilled, (state, action) => {
        state.branchesList = action.payload.branches;
      })
      .addCase(getUsers.pending, (state) => {
        state.loading = true;
      })
      .addCase(getUserStatistic.fulfilled, (state, action) => {
        state.statisticData = action.payload;
        state.statisticLoading = false;
      })
      .addCase(getUserStatistic.pending, (state) => {
        state.statisticLoading = true;
      });
  },
});

export const {
  setTableData,
  setUserList,
  setFilterData,
  setSelectedUser,
  setDrawerOpen,
  setDrawerClose,
} = usersSlice.actions;

export default usersSlice.reducer;
