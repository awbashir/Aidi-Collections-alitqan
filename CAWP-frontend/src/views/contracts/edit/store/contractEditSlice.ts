import { createSlice, createAsyncThunk } from "@reduxjs/toolkit";
import {
  apiGetContractsDetails,
  apPutContract,
} from "@/services/ContractsService";

export const SLICE_NAME = "contractEdit";

export type Contract = {
  id: string;
  name: string;
  is_active: boolean;
};

type GetContractDetailsResponse = Contract;

type GetContractDetailsRequest = { id: string };

export type ContractEditState = {
  loading: boolean;
  contract: Contract;
};

export const getContract = createAsyncThunk(
  SLICE_NAME + "/getContract",
  async (data: GetContractDetailsRequest) => {
    const response = await apiGetContractsDetails<
      GetContractDetailsResponse,
      GetContractDetailsRequest
    >(data);
    return response.data;
  }
);

export const updateContract = async <T, U extends Record<string, unknown>>(
  data: U
) => {
  const response = await apPutContract<T, U>(data);
  return response.data;
};

const initialState: ContractEditState = {
  loading: true,
  contract: {} as Contract,
};

const contractEditSlice = createSlice({
  name: `${SLICE_NAME}/state`,
  initialState,
  reducers: {},
  extraReducers: (builder) => {
    builder.addCase(getContract.pending, (state) => {
      state.loading = true;
    });
    builder.addCase(getContract.fulfilled, (state, action) => {
      state.loading = false;
      state.contract = action.payload;
    });
    builder.addCase(getContract.rejected, (state) => {
      state.loading = false;
    });
  },
});

export default contractEditSlice.reducer;
