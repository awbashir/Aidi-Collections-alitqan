import { createSlice, createAsyncThunk } from "@reduxjs/toolkit";
import {
  apiGetRolesDetails,
  apiPutRoles,
  apiGetContracts,
  apiGetCommentReachability,
} from "@/services/PermissionsService";

export const SLICE_NAME = "roleEdit";

export type Portfolio = {
  id: string;
  name: string;
  is_active: boolean;
};

type GetPortfolioDetailsResponse = Portfolio;

type GetPortfolioDetailsRequest = { id: string };

export type PortfolioEditState = {
  loading: boolean;
  portfolio: Portfolio;
  contractsOptions: object[];
  reachabilityOptions: object[];
};

export const getDetails = createAsyncThunk(
  SLICE_NAME + "/getDetails",
  async (data: GetPortfolioDetailsRequest) => {
    const response = await apiGetRolesDetails<
      GetPortfolioDetailsResponse,
      GetPortfolioDetailsRequest
    >(data);
    return response.data;
  }
);

export const updateDetails = async <T, U extends Record<string, unknown>>(
  data: U
) => {
  const response = await apiPutRoles<T, U>(data);
  return response.data;
};

export const getContracts = createAsyncThunk(
  SLICE_NAME + "/getContracts",
  async () => {
    const response = await apiGetContracts<branch, {}>({});
    return response.data.contracts;
  }
);

export const getCommentReachability = createAsyncThunk(
  SLICE_NAME + "/getCommentReachability",
  async () => {
    const response = await apiGetCommentReachability<branch, {}>({});
    return response.data.comment_reachabilitys;
  }
);

const initialState: PortfolioEditState = {
  loading: true,
  portfolio: {} as Portfolio,
  contractsOptions: [],
  reachabilityOptions: [],
};

const portfolioEditSlice = createSlice({
  name: `${SLICE_NAME}/state`,
  initialState,
  reducers: {},
  extraReducers: (builder) => {
    builder.addCase(getDetails.pending, (state) => {
      state.loading = true;
    });
    builder.addCase(getDetails.fulfilled, (state, action) => {
      state.loading = false;
      state.portfolio = action.payload;
    });
    builder.addCase(getDetails.rejected, (state) => {
      state.loading = false;
    });

    builder.addCase(getContracts.fulfilled, (state, action) => {
      state.contractsOptions = action.payload;
    });

    builder.addCase(getCommentReachability.fulfilled, (state, action) => {
      state.reachabilityOptions = action.payload;
    });
  },
});

export default portfolioEditSlice.reducer;
