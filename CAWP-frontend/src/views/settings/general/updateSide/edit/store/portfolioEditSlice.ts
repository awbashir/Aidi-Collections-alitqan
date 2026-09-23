import { createSlice, createAsyncThunk } from "@reduxjs/toolkit";
import {
  apiGetUpdateSidesDetails,
  apiPutUpdateSides,
} from "@/services/SettingsService";

export const SLICE_NAME = "updateSidesEdit";

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

export const getDetails = createAsyncThunk(
  SLICE_NAME + "/getDetails",
  async (data: GetPortfolioDetailsRequest) => {
    const response = await apiGetUpdateSidesDetails<
      GetPortfolioDetailsResponse,
      GetPortfolioDetailsRequest
    >(data);
    return response.data;
  }
);

export const updateDetails = async <T, U extends Record<string, unknown>>(
  data: U
) => {
  const response = await apiPutUpdateSides<T, U>(data);
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
  },
});

export default portfolioEditSlice.reducer;
