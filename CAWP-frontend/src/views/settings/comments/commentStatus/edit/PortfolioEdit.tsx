import { useEffect } from "react";
import Container from "@/components/shared/Container";
import Loading from "@/components/shared/Loading";

import reducer, {
  getDetails,
  useAppDispatch,
  useAppSelector,
  updateDetails,
  getContracts,
  getCommentReachability,
} from "./store";

import { injectReducer } from "@/store";
import isEmpty from "lodash/isEmpty";
import useQuery from "@/utils/hooks/useQuery";
import PortfolioForm, { FormModel, SetSubmitting } from "../form/PortfolioForm";
import { useLocation, useNavigate } from "react-router-dom";
import toast from "@/components/ui/toast";
import Notification from "@/components/ui/Notification";
import { t } from "i18next";
import ToastNotification from "@/components/shared/myComponents/ToastNotification";

injectReducer("commentStatusEdit", reducer);

const PortfolioEdit = () => {
  const dispatch = useAppDispatch();
  const query = useQuery();
  const navigate = useNavigate();

  useEffect(() => {
    fetchData();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const data = useAppSelector(
    (state) => state.commentStatusEdit.data.portfolio
  );

  const contractsOptions = useAppSelector(
    (state) => state.commentStatusEdit.data.contractsOptions
  );

  const reachabilityOptions = useAppSelector(
    (state) => state.commentStatusEdit.data.reachabilityOptions
  );

  const loading = useAppSelector(
    (state) => state.commentStatusEdit.data.loading
  );

  const fetchData = () => {
    const id = query.get("id");
    if (id) {
      dispatch(getDetails({ id }));
      dispatch(getContracts());
      dispatch(getCommentReachability());
    }
  };

  const handleDiscard = () => {
    navigate("/settings/comments/commentStatus");
  };

  const handleFormSubmit = async (
    values: FormModel,
    setSubmitting: SetSubmitting
  ) => {
    setSubmitting(true);
    const success = await updateDetails(values)
      .then((response) => {
        if (response.id) {
          ToastNotification(
            t("settings.comments.commentStatus.successEdit"),
            t("settings.comments.commentStatus.edit"),
            "success"
          );
          navigate("/settings/comments/commentStatus");
        } else {
          ToastNotification(
            response?.message || t("settings.comments.commentStatus.failEdit"),
            t("settings.comments.commentStatus.edit"),
            "danger"
          );
        }
      })
      .catch((error) => {
        ToastNotification(
          t("settings.comments.commentStatus.failEdit"),
          t("settings.comments.commentStatus.edit"),
          "danger"
        );
      });

    setSubmitting(false);
  };

  return (
    <Container className="h-full">
      <Loading loading={loading}>
        {!isEmpty(data) && (
          <PortfolioForm
            type="edit"
            contracts={contractsOptions}
            reachability={reachabilityOptions}
            initialData={data}
            onDiscard={handleDiscard}
            onDeleted={handleDiscard}
            onFormSubmit={handleFormSubmit}
          />
        )}
      </Loading>
    </Container>
  );
};

export default PortfolioEdit;
