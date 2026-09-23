import { createSlice, createAsyncThunk } from "@reduxjs/toolkit";
import {
  apiGetCrmUserDetails,
  apiDeleteCrmUser,
  apiPutActivateUser,
  apiPutDeactivateUser,
  apPutCrmUser,
  apiGetLoginLogsByUserId,
} from "@/services/UserService";

export const SLICE_NAME = "crmCustomerDetails";

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

export type OrderHistory = {
  id: string;
  item: string;
  status: string;
  amount: number;
  date: number;
};

export type PaymentMethod = {
  cardHolderName: string;
  cardType: string;
  expMonth: string;
  expYear: string;
  last4Number: string;
  primary: boolean;
};

export type Subscription = {
  plan: string;
  status: string;
  billing: string;
  nextPaymentDate: number;
  amount: number;
};

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

  img: string;
  lastOnline: number;
  status: string;
  personalInfo: PersonalInfo;
};

type GetCrmCustomerDetailsResponse = User & {
  orderHistory?: OrderHistory[];
  paymentMethod?: PaymentMethod[];
  subscription?: Subscription[];
};

type GetCrmCustomerDetailsRequest = { id: string };

// eslint-disable-next-line @typescript-eslint/ban-types
type DeleteCrmCustomerResponse = {};

type DeleteCrmCustomerRequest = { id: string };

export type CustomerDetailState = {
  loading: boolean;
  loadingLoginLogs: boolean;
  profileData: Partial<User>;
  LoginLogsData: OrderHistory[];
  subscriptionData: Subscription[];
  paymentHistoryData: OrderHistory[];
  paymentMethodData: PaymentMethod[];
  deletePaymentMethodDialog: boolean;
  editPaymentMethodDialog: boolean;
  editCustomerDetailDialog: boolean;
  selectedCard: Partial<PaymentMethod>;
};

export const getCustomer = createAsyncThunk(
  SLICE_NAME + "/getCustomer",
  async (data: GetCrmCustomerDetailsRequest) => {
    const response = await apiGetCrmUserDetails<
      GetCrmCustomerDetailsResponse,
      GetCrmCustomerDetailsRequest
    >(data);
    return response.data;
  }
);
export const getLoginLogs = createAsyncThunk(
  SLICE_NAME + "/getLoginLogs",
  async (data: GetCrmCustomerDetailsRequest) => {
    const response = await apiGetLoginLogsByUserId(data);
    return response.data;
  }
);

export const deleteCustomer = createAsyncThunk(
  SLICE_NAME + "/deleteCustomer",
  async (data: DeleteCrmCustomerRequest) => {
    const response = await apiDeleteCrmUser<
      DeleteCrmCustomerResponse,
      DeleteCrmCustomerRequest
    >(data);
    return response.data;
  }
);

export const putCustomer = createAsyncThunk(
  SLICE_NAME + "/putCustomer",
  async (data: User) => {
    const response = await apPutCrmUser(data);
    return response.data;
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

const initialState: CustomerDetailState = {
  loading: true,
  loadingLoginLogs: true,
  profileData: {},
  LoginLogsData: [],
  subscriptionData: [],
  paymentHistoryData: [],
  paymentMethodData: [],
  deletePaymentMethodDialog: false,
  editPaymentMethodDialog: false,
  editCustomerDetailDialog: false,
  selectedCard: {},
};

const customerDetailSlice = createSlice({
  name: `${SLICE_NAME}/state`,
  initialState,
  reducers: {
    updatePaymentMethodData: (state, action) => {
      state.paymentMethodData = action.payload;
    },
    updateProfileData: (state, action) => {
      state.profileData = action.payload;
    },
    openDeletePaymentMethodDialog: (state) => {
      state.deletePaymentMethodDialog = true;
    },
    closeDeletePaymentMethodDialog: (state) => {
      state.deletePaymentMethodDialog = false;
    },
    openEditPaymentMethodDialog: (state) => {
      state.editPaymentMethodDialog = true;
    },
    closeEditPaymentMethodDialog: (state) => {
      state.editPaymentMethodDialog = false;
    },
    openEditCustomerDetailDialog: (state) => {
      state.editCustomerDetailDialog = true;
    },
    closeEditCustomerDetailDialog: (state) => {
      state.editCustomerDetailDialog = false;
    },
    updateSelectedCard: (state, action) => {
      state.selectedCard = action.payload;
    },
  },
  extraReducers: (builder) => {
    builder
      .addCase(getCustomer.fulfilled, (state, action) => {
        state.loading = false;
        state.profileData = action.payload;
        state.subscriptionData = action.payload?.subscription || [];
        state.paymentHistoryData = action.payload?.orderHistory || [];
        state.paymentMethodData = action.payload?.paymentMethod || [];
      })
      .addCase(getCustomer.pending, (state) => {
        state.loading = true;
      })
      .addCase(getLoginLogs.fulfilled, (state, action) => {
        state.loadingLoginLogs = false;
        state.LoginLogsData = action.payload.loginLog;
      })
      .addCase(getLoginLogs.pending, (state) => {
        state.loadingLoginLogs = true;
      });
  },
});

export const {
  updatePaymentMethodData,
  updateProfileData,
  openDeletePaymentMethodDialog,
  closeDeletePaymentMethodDialog,
  openEditPaymentMethodDialog,
  closeEditPaymentMethodDialog,
  openEditCustomerDetailDialog,
  closeEditCustomerDetailDialog,
  updateSelectedCard,
} = customerDetailSlice.actions;

export default customerDetailSlice.reducer;
