import { useEffect } from "react";
import Container from "@/components/shared/Container";
import Loading from "@/components/shared/Loading";

import reducer, {
  useAppDispatch,
  useAppSelector,
  /*   getNationalities,
  getRoles,
  getBranches, */
  getContracts,
  getCommentReachability,
  addNew,
} from "./store";

import { injectReducer } from "@/store";
import isEmpty from "lodash/isEmpty";
import useQuery from "@/utils/hooks/useQuery";
import UserForm, { FormModel, SetSubmitting } from "../form/PortfolioForm";
import { useLocation, useNavigate } from "react-router-dom";
import toast from "@/components/ui/toast";
import Notification from "@/components/ui/Notification";
import ToastNotification from "@/components/shared/myComponents/ToastNotification";
import { useTranslation } from "react-i18next";

injectReducer("commentStatusNew", reducer);

const UserNew = () => {
  const dispatch = useAppDispatch();
  const navigate = useNavigate();
  const { t } = useTranslation();

  useEffect(() => {
    fetchData();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /* const naionalityOptions = useAppSelector(
    (state) => state.commentStatusNew.data.nationalityOptions
  );

  const roleOptions = useAppSelector(
    (state) => state.commentStatusNew.data.roleOptions
  );
  const branchOptions = useAppSelector(
    (state) => state.commentStatusNew.data.branchOptions
  ); */

  const contractsOptions = useAppSelector(
    (state) => state.commentStatusNew.data.contractsOptions
  );

  const reachabilityOptions = useAppSelector(
    (state) => state.commentStatusNew.data.reachabilityOptions
  );

  const loading = useAppSelector(
    (state) => state.commentStatusNew.data.loading
  );

  const fetchData = () => {
    /*  dispatch(getNationalities());
    dispatch(getRoles());
    dispatch(getBranches()); */
    dispatch(getContracts());
    dispatch(getCommentReachability());
  };

  const handleDiscard = () => {
    navigate("/settings/comments/commentStatus");
  };

  const handleFormSubmit = async (
    values: FormModel,
    setSubmitting: SetSubmitting
  ) => {
    setSubmitting(true);
    //delete id from values
    delete values.id;
    const success = await addNew(values)
      .then((response) => {
        if (response.id) {
          ToastNotification(
            t("settings.comments.commentStatus.successAdd"),
            t("settings.comments.commentStatus.new"),
            "success"
          );
          navigate("/settings/comments/commentStatus");
        } else {
          ToastNotification(
            t("settings.comments.commentStatus.failAdd"),
            t("settings.comments.commentStatus.new"),
            "danger"
          );
        }
      })
      .catch((error) => {
        ToastNotification(
          t("settings.comments.commentStatus.failAdd"),
          t("settings.comments.commentStatus.new"),
          "danger"
        );
      });

    setSubmitting(false);
  };

  return (
    <Container className="h-full">
      <UserForm
        type="New"
        /*  nationalities={naionalityOptions}
        roles={roleOptions}
        branches={branchOptions} */
        contracts={contractsOptions}
        reachability={reachabilityOptions}
        onDiscard={handleDiscard}
        onFormSubmit={handleFormSubmit}
      />
    </Container>
  );
};

export default UserNew;
