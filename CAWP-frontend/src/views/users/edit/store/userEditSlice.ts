import { createSlice, createAsyncThunk } from "@reduxjs/toolkit";
import {
  apiGetCrmUserDetails,
  apPutCrmUser,
  apiGetNationalities,
  apiGetRoles,
  apiGetBranches,
  apiGetContracts,
} from "@/services/UserService";
import exp from "constants";

export const SLICE_NAME = "UserEdit";

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

export type UserEditState = {
  loading: boolean;
  user: User;
  nationalityOptions: nationality[];
  roleOptions: role[];
  branchOptions: branch[];
  contractOptions: contract[];
};

export const getUser = createAsyncThunk(
  SLICE_NAME + "/getUser",
  async (data: GetUserDetailsRequest) => {
    const response = await apiGetCrmUserDetails<
      GetCrmCustomerDetailsResponse,
      GetCrmCustomerDetailsRequest
    >(data);
    return response.data;
  }
);

export const updateUser = async <T, U extends Record<string, unknown>>(
  data: U
) => {
  const response = await apPutCrmUser<T, U>(data);
  return response.data;
};

export const getNationalities = createAsyncThunk(
  SLICE_NAME + "/getNationalities",
  async () => {
    const response = await apiGetNationalities<nationality, {}>({});
    return response.data.nationalities;
  }
);

export const getRoles = createAsyncThunk(SLICE_NAME + "/getRoles", async () => {
  const response = await apiGetRoles<role, {}>({});
  return response.data.roles;
});
export const getBranches = createAsyncThunk(
  SLICE_NAME + "/getBranches",
  async () => {
    const response = await apiGetBranches<branch, {}>({});
    return response.data.branches;
  }
);

export const getContracts = createAsyncThunk(
  SLICE_NAME + "/getContracts",
  async () => {
    const response = await apiGetContracts<contract, {}>({});
    return response.data.contracts;
  }
);

const initialState: UserEditState = {
  loading: true,
  user: {} as User,
  nationalityOptions: [],
  roleOptions: [],
  branchOptions: [],
  contractOptions: [],
};

const userEditSlice = createSlice({
  name: `${SLICE_NAME}/state`,
  initialState,
  reducers: {},
  extraReducers: (builder) => {
    builder.addCase(getUser.pending, (state) => {
      state.loading = true;
    });
    builder.addCase(getUser.fulfilled, (state, action) => {
      state.loading = false;
      state.user = action.payload;
    });
    builder.addCase(getUser.rejected, (state) => {
      state.loading = false;
    });

    builder.addCase(getNationalities.fulfilled, (state, action) => {
      state.nationalityOptions = action.payload;
    });

    builder.addCase(getRoles.fulfilled, (state, action) => {
      state.roleOptions = action.payload;
    });

    builder.addCase(getBranches.fulfilled, (state, action) => {
      state.branchOptions = action.payload;
    });
    builder.addCase(getContracts.fulfilled, (state, action) => {
      state.contractOptions = action.payload;
    });
  },
});

export default userEditSlice.reducer;
