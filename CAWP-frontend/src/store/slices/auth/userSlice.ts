import { createSlice, PayloadAction } from "@reduxjs/toolkit";
import { SLICE_BASE_NAME } from "./constants";

export type UserState = {
  avatar?: string;
  username?: string;
  email?: string;
  authority?: string[];
  name?: string;
  permission_name?: string;
  permissions?: object;
  id?: string;
};

const initialState: UserState = {
  avatar: "",
  username: "",
  email: "",
  name: "",
  authority: [],
  permission_name: "",
  permissions: {},
  id: "",
};

const userSlice = createSlice({
  name: `${SLICE_BASE_NAME}/user`,
  initialState,
  reducers: {
    setUser(state, action: PayloadAction<UserState>) {
      state.avatar = action.payload?.avatar;
      state.email = action.payload?.email;
      state.username = action.payload?.user_name;
      state.authority = action.payload?.authority;
      state.name = action.payload?.frist_name + " " + action.payload?.last_name;
      state.permission_name = action.payload?.role?.name;
      state.permissions = action.payload?.role?.permission;
      state.id = action.payload?.id;
    },
    /* Phase 3: GET /v2/me refreshes the effective permissions (and the role
     * name) without a new sign-in, so a grant change reaches the nav at once. */
    setPermissions(
      state,
      action: PayloadAction<{ permissions: Record<string, boolean>; roleName?: string | null }>
    ) {
      state.permissions = action.payload.permissions;
      if (action.payload.roleName) state.permission_name = action.payload.roleName;
    },
  },
});

export const { setUser, setPermissions } = userSlice.actions;
export default userSlice.reducer;
