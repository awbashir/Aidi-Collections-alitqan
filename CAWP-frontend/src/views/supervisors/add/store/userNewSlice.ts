import { createSlice, createAsyncThunk } from "@reduxjs/toolkit";
import {
  /* apiPostBranch,
    apiGetNationalities,
  apiGetRoles, */
  apiGetPortfolios,
  apiGetBranches,
  apiGetUsers,
  apiGetContracts,
  apiPostSupervisor,
  apiPostSupervisorContract,
  apiPostSupervisorUser,
} from "@/services/SuperviorsService";
import exp from "constants";

export const SLICE_NAME = "UserNew";

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

type contracts = {
  id: string;
  is_active: boolean;
  title: string;
  created_at: Date;
  updated_at: Date;
};

type supervisoruser = User;

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
};

type GetUserDetailsResponse = User;

type GetUserDetailsRequest = { id: string };

export type UserNewState = {
  loading: boolean;
  user: User;
  nationalityOptions: nationality[];
  roleOptions: role[];
  branchOptions: branch[];
  userOptions: User[];
  contractOptions: contracts[];
};

export const updateUser = async <T, U extends Record<string, unknown>>(
  data: U
) => {
  const response = await apPutCrmUser<T, U>(data);
  return response.data;
};

/* export const newBranch = async <T, U extends Record<string, unknown>>(
  data: U
) => {
  const response = await apiPostBranch<T, U>(data);
  return response.data;
}; */

/* export const getNationalities = createAsyncThunk(
  SLICE_NAME + "/getNationalities",
  async () => {
    const response = await apiGetNationalities<nationality, {}>({});
    return response.data.nationalities;
  }
);

export const getRoles = createAsyncThunk(SLICE_NAME + "/getRoles", async () => {
  const response = await apiGetRoles<role, {}>({});
  return response.data.roles;
}); */

export const newSupervior = async <T, U extends Record<string, unknown>>(
  data: U
) => {
  const response = await apiPostSupervisor<T, U>(data);
  return response.data;
};

export const newSuperviorContract = async <
  T,
  U extends Record<string, unknown>
>(
  data: U
) => {
  const response = await apiPostSupervisorContract<T, U>(data);
  return response.data;
};

export const newSuperviorUser = async <T, U extends Record<string, unknown>>(
  data: U
) => {
  const response = await apiPostSupervisorUser<T, U>(data);
  return response.data;
};

export const getBranches = createAsyncThunk(
  SLICE_NAME + "/getBranches",
  async () => {
    const response = await apiGetBranches<branch, {}>({});
    return response.data.branches;
  }
);

export const getUsers = createAsyncThunk(SLICE_NAME + "/getUsers", async () => {
  const response = await apiGetUsers<User, {}>({});
  return response.data.users;
});

export const getContracts = createAsyncThunk(
  SLICE_NAME + "/getContracts",
  async () => {
    const response = await apiGetContracts<contracts, {}>({});
    return response.data.contracts;
  }
);

const initialState: UserNewState = {
  loading: true,
  user: {} as User,
  nationalityOptions: [],
  roleOptions: [],
  branchOptions: [],
  userOptions: [],
  contractOptions: [],
};

const userNewSlice = createSlice({
  name: `${SLICE_NAME}/state`,
  initialState,
  reducers: {},
  extraReducers: (builder) => {
    /* builder.addCase(getNationalities.fulfilled, (state, action) => {
      state.nationalityOptions = action.payload;
    });

    builder.addCase(getRoles.fulfilled, (state, action) => {
      state.roleOptions = action.payload;
    }); */

    builder.addCase(getBranches.fulfilled, (state, action) => {
      state.branchOptions = action.payload;
    });

    builder.addCase(getUsers.fulfilled, (state, action) => {
      state.userOptions = action.payload;
    });

    builder.addCase(getContracts.fulfilled, (state, action) => {
      state.contractOptions = action.payload;
    });
  },
});

export default userNewSlice.reducer;
