import { createSlice, createAsyncThunk } from "@reduxjs/toolkit";
import { apiGetClientsDetails, apPutClient } from "@/services/ClientsService";

export const SLICE_NAME = "clientEdit";

export type Client = {
  id: string;
  name: string;
  is_active: boolean;
};

type GetClientDetailsResponse = Client;

type GetClientDetailsRequest = { id: string };

export type ClientEditState = {
  loading: boolean;
  client: Client;
};

export const getClient = createAsyncThunk(
  SLICE_NAME + "/getClient",
  async (data: GetClientDetailsRequest) => {
    const response = await apiGetClientsDetails<
      GetClientDetailsResponse,
      GetClientDetailsRequest
    >(data);
    return response.data;
  }
);

export const updateClient = async <T, U extends Record<string, unknown>>(
  data: U
) => {
  const response = await apPutClient<T, U>(data);
  return response.data;
};

const initialState: ClientEditState = {
  loading: true,
  client: {} as Client,
};

const clientEditSlice = createSlice({
  name: `${SLICE_NAME}/state`,
  initialState,
  reducers: {},
  extraReducers: (builder) => {
    builder.addCase(getClient.pending, (state) => {
      state.loading = true;
    });
    builder.addCase(getClient.fulfilled, (state, action) => {
      state.loading = false;
      state.client = action.payload;
    });
    builder.addCase(getClient.rejected, (state) => {
      state.loading = false;
    });
  },
});

export default clientEditSlice.reducer;
