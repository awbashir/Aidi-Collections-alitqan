import { useEffect } from "react";
import Container from "@/components/shared/Container";
import Loading from "@/components/shared/Loading";

import reducer, {
  useAppDispatch,
  useAppSelector,
  getNationalities,
  getContracts,
  getRoles,
  getBranches,
  newUser,
} from "./store";

import { injectReducer } from "@/store";
import isEmpty from "lodash/isEmpty";
import useQuery from "@/utils/hooks/useQuery";
import UserForm, { FormModel, SetSubmitting } from "../form/UserForm";
import { useLocation, useNavigate } from "react-router-dom";
import toast from "@/components/ui/toast";
import Notification from "@/components/ui/Notification";
import { useTranslation } from "react-i18next";

injectReducer("userNew", reducer);

const UserNew = () => {
  const { t } = useTranslation();
  const dispatch = useAppDispatch();
  const navigate = useNavigate();

  useEffect(() => {
    fetchData();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const naionalityOptions = useAppSelector(
    (state) => state.userNew.data.nationalityOptions
  );

  const roleOptions = useAppSelector((state) => state.userNew.data.roleOptions);
  const branchOptions = useAppSelector(
    (state) => state.userNew.data.branchOptions
  );
  const contractOptions = useAppSelector(
    (state) => state.userNew.data.contractOptions
  );
  const loading = useAppSelector((state) => state.userNew.data.loading);

  const fetchData = () => {
    dispatch(getNationalities());
    dispatch(getRoles());
    dispatch(getBranches());
    dispatch(getContracts());
  };

  const handleDiscard = () => {
    navigate("/users");
  };

  const handleFormSubmit = async (
    values: FormModel,
    setSubmitting: SetSubmitting
  ) => {
    setSubmitting(true);
    //delete id from values
    delete values.id;
    // The form has no active switch; a new account must be able to sign in.
    values.is_active = true;
    const result = await newUser<{ status?: boolean; message?: string }, FormModel>(values);
    setSubmitting(false);
    // Legacy errors come back as HTTP 201 with status:false (core/http.error_response).
    if (result?.status === false) {
      toast.push(
        <Notification title={t("users.addNew")} type="danger" duration={4000}>
          {result.message}
        </Notification>,
        { placement: "top-center" }
      );
      return;
    }
    popNotification("New");
  };

  const popNotification = (keyword: string) => {
    toast.push(
      <Notification title={t("users.addNew")} type="success" duration={2500}>
        {t(`users.addSuccess`)}
      </Notification>,
      {
        placement: "top-center",
      }
    );
    navigate("/users");
  };

  return (
    <Container className="h-full">
      <UserForm
        type="new"
        nationalities={naionalityOptions}
        roles={roleOptions}
        contracts={contractOptions}
        branches={branchOptions}
        onDiscard={handleDiscard}
        onFormSubmit={handleFormSubmit}
      />
    </Container>
  );
};

export default UserNew;
