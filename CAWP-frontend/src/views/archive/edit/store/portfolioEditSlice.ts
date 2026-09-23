import { createSlice, createAsyncThunk } from "@reduxjs/toolkit";
import {
  apiGetPortfoliosDetails,
  apPutPortfolio,
} from "@/services/PortfoliosService";

export const SLICE_NAME = "archiveEdit";

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
      state.portfolio = action.payload;
    });
    builder.addCase(getPortfolio.rejected, (state) => {
      state.loading = false;
    });
  },
});

export default portfolioEditSlice.reducer;
