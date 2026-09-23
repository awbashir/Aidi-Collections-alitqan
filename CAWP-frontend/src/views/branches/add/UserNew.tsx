import { useEffect } from "react";
import Container from "@/components/shared/Container";
import Loading from "@/components/shared/Loading";

import reducer, {
  useAppDispatch,
  useAppSelector,
  /*  getNationalities,
  getRoles, */
  getBranches,
  newBranch,
} from "./store";

import { injectReducer } from "@/store";
import isEmpty from "lodash/isEmpty";
import useQuery from "@/utils/hooks/useQuery";
import PortfolioForm, { FormModel, SetSubmitting } from "../form/PortfolioForm";
import { useLocation, useNavigate } from "react-router-dom";
import toast from "@/components/ui/toast";
import Notification from "@/components/ui/Notification";
import ToastNotification from "@/components/shared/myComponents/ToastNotification";
import { useTranslation } from "react-i18next";

injectReducer("userNew", reducer);

const UserNew = () => {
  const dispatch = useAppDispatch();
  const navigate = useNavigate();

  useEffect(() => {
    fetchData();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // const naionalityOptions = useAppSelector(
  //   (state) => state.userNew.data.nationalityOptions
  // );

  // const roleOptions = useAppSelector((state) => state.userNew.data.roleOptions);
  const branchOptions = useAppSelector(
    (state) => state.userNew.data.branchOptions
  );
  const loading = useAppSelector((state) => state.userNew.data.loading);
  const { t } = useTranslation();

  const fetchData = () => {
    // dispatch(getNationalities());
    // dispatch(getRoles());
    dispatch(getBranches());
  };

  const handleDiscard = () => {
    navigate("/branch");
  };

  const handleFormSubmit = async (
    values: FormModel,
    setSubmitting: SetSubmitting
  ) => {
    setSubmitting(true);
    //delete id from values
    delete values.id;
    const success = await newBranch(values);
    setSubmitting(false);
    if (success) {
      popNotification("New");
    }
  };

  const popNotification = (keyword: string) => {
    ToastNotification(t("branches.successAdd"), t("branches.add"), "success");
    navigate("/branch");
  };

  return (
    <Container className="h-full">
      <PortfolioForm
        type="New"
        /*  nationalities={naionalityOptions}
        roles={roleOptions} */
        branches={branchOptions}
        onDiscard={handleDiscard}
        onFormSubmit={handleFormSubmit}
      />
    </Container>
  );
};

export default UserNew;
