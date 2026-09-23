import { createSlice, createAsyncThunk, current } from "@reduxjs/toolkit";
import {
  apiGetClientsDetails,
  apPutClient,
  apiPostClintContactTypes,
  apiGetContactsIdentityNumber,
  apiPostComments,
  apiGetComments,
  apiPostPayments,
  apiGetPayments,
  apiPostPaymentDate,
  apiGetPaymentDate,
  apiGetSpecifyUsers,
  apiDeleteComments,
  apiDeleteClintContact,
  apiPutClintContactUnsetDefaultByID,
  apiPutClintContactDefaultByID,
  apiPutClintsDelinquencyByID,
  //apiGetCrmUserDetails,
  //apiDeleteCrmUser,
  // apPutCrmUser,
} from "@/services/ClientsService";
import { object } from "yup";

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
  clientData: Partial<User>;
  ContactData: object[];
  comments: Object[];
  payments: Object[];
  paymentDate: Object[];
  SpecifyUsers: Object[];
  loadingPaymentDate: boolean;
  loadingPayment: boolean;
  loadingComments: boolean;
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
    const response = await apiGetClientsDetails<
      GetCrmCustomerDetailsResponse,
      GetCrmCustomerDetailsRequest
    >(data);
    return response.data;
  }
);

export const putClient = createAsyncThunk(
  SLICE_NAME + "/putClient",
  async (data: User) => {
    const response = await apPutClient(data);
    return response.data;
  }
);

export const postClintContactTypes = createAsyncThunk(
  SLICE_NAME + "/postClintContactTypes",
  async (data: User) => {
    const response = await apiPostClintContactTypes(data);
    return response.data;
  }
);

export const getContactsIdentityNumber = createAsyncThunk(
  SLICE_NAME + "/getContactsIdentityNumber",
  async (data: User) => {
    const response = await apiGetContactsIdentityNumber(data);
    return response.data.contacts;
  }
);

export const postComments = createAsyncThunk(
  SLICE_NAME + "/postComments",
  async (data: User) => {
    const response = await apiPostComments(data);
    return response.data;
  }
);

export const deleteComments = async <T, U extends Record<string, unknown>>(
  data: U
) => {
  const response = await apiDeleteComments<T, U>(data);
  return response.data;
};

//apiDeleteClintContact
export const deleteClintContact = async <T, U extends Record<string, unknown>>(
  data: U
) => {
  const response = await apiDeleteClintContact<T, U>(data);
  return response.data;
};

//apiPutClintContactUnsetDefaultByID
export const putClintContactUnsetDefaultByID = async <
  T,
  U extends Record<string, unknown>
>(
  data: U
) => {
  const response = await apiPutClintContactUnsetDefaultByID<T, U>(data);
  return response.data;
};

//apiPutClintsDelinquencyByID
export const putClintsDelinquencyByID = async <
  T,
  U extends Record<string, unknown>
>(
  data: U
) => {
  const response = await apiPutClintsDelinquencyByID<T, U>(data);
  return response.data;
};

//apiPutClintContactDefaultByID
export const putClintContactDefaultByID = async <
  T,
  U extends Record<string, unknown> 
>(
  data: U
) => {
  const response = await apiPutClintContactDefaultByID<T, U>(data);
  return response.data;
};


export const getComments = createAsyncThunk(
  SLICE_NAME + "/getComments",
  async (data: User) => {
    const response = await apiGetComments(data);
    return response.data;
  }
);

export const postPayments = createAsyncThunk(
  SLICE_NAME + "/postPayments",
  async (data: User) => {
    const response = await apiPostPayments(data);
    return response.data;
  }
);

export const getPayments = createAsyncThunk(
  SLICE_NAME + "/getPayments",
  async (data: User) => {
    const response = await apiGetPayments(data);
    return response.data;
  }
);

export const postPaymentDate = createAsyncThunk(
  SLICE_NAME + "/postPaymentDate",
  async (data: User) => {
    const response = await apiPostPaymentDate(data);
    return response.data;
  }
);

export const getPaymentDate = createAsyncThunk(
  SLICE_NAME + "/getPaymentDate",
  async (data: User) => {
    const response = await apiGetPaymentDate(data);
    return response.data;
  }
);

export const getSpecifyUsers = createAsyncThunk(
  SLICE_NAME + "/getSpecifyUsers",
  async (data: User) => {
    const response = await apiGetSpecifyUsers(data);
    return response.data;
  }
);

/* export const deleteCustomer = createAsyncThunk(
  SLICE_NAME + "/deleteCustomer",
  async (data: DeleteCrmCustomerRequest) => {
    const response = await apiDeleteCrmUser<
      DeleteCrmCustomerResponse,
      DeleteCrmCustomerRequest
    >(data);
    return response.data;
  }
); */

/* export const putCustomer = createAsyncThunk(
  SLICE_NAME + "/putCustomer",
  async (data: User) => {
    const response = await apPutCrmUser(data);
    return response.data;
  }
); */

const initialState: CustomerDetailState = {
  loading: true,
  clientData: {},
  ContactData: [],
  ContactDataLoading: false,
  comments: [],
  payments: [],
  paymentDate: [],
  SpecifyUsers: [],
  subscriptionData: [],
  paymentHistoryData: [],
  paymentMethodData: [],
  deletePaymentMethodDialog: false,
  editPaymentMethodDialog: false,
  editCustomerDetailDialog: false,
  selectedCard: {},
  loadingPaymentDate: false,
  loadingPayment: false,
  loadingComments: false,
};

const customerDetailSlice = createSlice({
  name: `${SLICE_NAME}/state`,
  initialState,
  reducers: {
    updatePaymentMethodData: (state, action) => {
      state.paymentMethodData = action.payload;
    },
    updateProfileData: (state, action) => {
      state.clientData = action.payload;
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
        state.clientData = action.payload;
        state.subscriptionData = action.payload?.subscription || [];
        state.paymentHistoryData = action.payload?.orderHistory || [];
        state.paymentMethodData = action.payload?.paymentMethod || [];
      })
      .addCase(getCustomer.pending, (state) => {
        state.loading = true;
      })
      .addCase(getCustomer.rejected, (state) => {
        state.loading = false;
      })
      .addCase(putClient.fulfilled, (state, action) => {
        state.clientData = action.payload;
      })
      .addCase(putClient.pending, (state) => {
        state.loading = true;
      })
      .addCase(putClient.rejected, (state) => {
        state.loading = false;
      })
      .addCase(getContactsIdentityNumber.fulfilled, (state, action) => {
        state.ContactData = action.payload;
        state.ContactDataLoading = false;
      })
      .addCase(getContactsIdentityNumber.pending, (state) => {
        state.ContactDataLoading = true;
      })
      .addCase(getContactsIdentityNumber.rejected, (state) => {
        state.ContactDataLoading = false;
      })
      .addCase(postClintContactTypes.fulfilled, (state, action) => {
        const currentState = current(state);
        state.ContactData = [...state.ContactData, action.payload];
      })
      .addCase(postClintContactTypes.pending, (state) => {
        state.loading = true;
      })
      .addCase(postClintContactTypes.rejected, (state) => {
        state.loading = false;
      })
      .addCase(postComments.fulfilled, (state, action) => {
        state.loading = false;
      })
      .addCase(postComments.pending, (state) => {
        state.loading = true;
      })
      .addCase(postComments.rejected, (state) => {
        state.loading = false;
      })
      .addCase(getComments.fulfilled, (state, action) => {
        state.loadingComments = false;
        state.comments = action.payload.comments;
      })
      .addCase(getComments.pending, (state) => {
        state.loadingComments = true;
      })
      .addCase(getComments.rejected, (state) => {
        state.loadingComments = false;
      })
      .addCase(postPayments.fulfilled, (state, action) => {
        state.loading = false;
      })
      .addCase(postPayments.pending, (state) => {
        state.loading = true;
      })
      .addCase(postPayments.rejected, (state) => {
        state.loading = false;
      })
      .addCase(getPayments.fulfilled, (state, action) => {
        state.loadingPayment = false;
        state.payments = action.payload.payments;
      })
      .addCase(getPayments.pending, (state) => {
        state.loadingPayment = true;
      })
      .addCase(getPayments.rejected, (state) => {
        state.loadingPayment = false;
      })
      .addCase(postPaymentDate.fulfilled, (state, action) => {
        state.loading = false;
      })
      .addCase(postPaymentDate.pending, (state) => {
        state.loading = true;
      })
      .addCase(postPaymentDate.rejected, (state) => {
        state.loading = false;
      })
      .addCase(getPaymentDate.fulfilled, (state, action) => {
        state.loadingPaymentDate = false;
        state.paymentDate = action.payload.payment_dates;
      })
      .addCase(getPaymentDate.pending, (state) => {
        state.loadingPaymentDate = true;
      })
      .addCase(getPaymentDate.rejected, (state) => {
        state.loadingPaymentDate = false;
      })
      .addCase(getSpecifyUsers.fulfilled, (state, action) => {
        state.loading = false;
        state.SpecifyUsers = action.payload.sepcifyusers;
      })
      .addCase(getSpecifyUsers.pending, (state) => {
        state.loading = true;
      })
      .addCase(getSpecifyUsers.rejected, (state) => {
        state.loading = false;
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
