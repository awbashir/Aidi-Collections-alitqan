import { createSlice, createAsyncThunk } from "@reduxjs/toolkit";
import {
  apiGetPortfoliosDetails,
  apPutPortfolio,
  apiGetBranches,
  apiGetUsers,
  apiGetContracts,
} from "@/services/SuperviorsService";

export const SLICE_NAME = "supervisorEdit";

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
};

export const getPortfolio = createAsyncThunk(
  SLICE_NAME + "/getPortfolio",
  async (data: GetPortfolioDetailsRequest) => {
    const response = await apiGetPortfoliosDetails<
      GetPortfolioDetailsResponse,
      GetPortfolioDetailsRequest
    >(data);
    return response.data;
  }
);

export const updatePortfolio = async <T, U extends Record<string, unknown>>(
  data: U
) => {
  const response = await apPutPortfolio<T, U>(data);
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

const initialState: PortfolioEditState = {
  loading: true,
  portfolio: {} as Portfolio,
};

const portfolioEditSlice = createSlice({
  name: `${SLICE_NAME}/state`,
  initialState,
  reducers: {},
  extraReducers: (builder) => {
    builder.addCase(getPortfolio.pending, (state) => {
      state.loading = true;
    });
    builder.addCase(getPortfolio.fulfilled, (state, action) => {
      state.loading = false;
      var portfolio = action.payload;
      portfolio.branch_id = portfolio.user.branch_id;
      portfolio.contract_result = portfolio.contracts.map((contract) => ({
        value: contract.id,
        label: contract.name,
      }));
      portfolio.user_result = portfolio.users.map((user) => ({
        value: user.id,
        label: user.name,
      }));

      state.portfolio = action.payload;
    });
    builder.addCase(getPortfolio.rejected, (state) => {
      state.loading = false;
    });

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

export default portfolioEditSlice.reducer;
