import { forwardRef } from "react";
import {
  setUserList,
  putUser,
  setDrawerClose,
  useAppDispatch,
  useAppSelector,
  User,
} from "../store";
import cloneDeep from "lodash/cloneDeep";
import isEmpty from "lodash/isEmpty";
import UserForm, { FormikRef, FormModel } from "@/views/users/form/UserForm";
import dayjs from "dayjs";

const UserEditContent = forwardRef<FormikRef>((_, ref) => {
  const dispatch = useAppDispatch();

  const user = useAppSelector((state) => state.crmUsers.data.selectedUser);
  const data = useAppSelector((state) => state.crmUsers.data.userList);
  const { id } = user;

  const onFormSubmit = (values: FormModel) => {
    const {
      name,
      birthday,
      email,
      img,
      location,
      title,
      phoneNumber,
      facebook,
      twitter,
      pinterest,
      linkedIn,
    } = values;

    const basicInfo = { name, email, img };
    const personalInfo = {
      location,
      title,
      birthday: dayjs(birthday).format("DD/MM/YYYY"),
      phoneNumber,
      facebook,
      twitter,
      pinterest,
      linkedIn,
    };
    let newData = cloneDeep(data);
    let editedUser: Partial<User> = {};
    newData = newData.map((elm) => {
      if (elm.id === id) {
        elm = { ...elm, ...basicInfo };
        elm.personalInfo = { ...elm.personalInfo, ...personalInfo };
        editedUser = elm;
      }
      return elm;
    });
    if (!isEmpty(editedUser)) {
      dispatch(putUser(editedUser as User));
    }
    dispatch(setDrawerClose());
    dispatch(setUserList(newData));
  };

  return <UserForm ref={ref} user={user} onFormSubmit={onFormSubmit} />;
});

UserEditContent.displayName = "UserEditContent";

export type { FormikRef };

export default UserEditContent;
