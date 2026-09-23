import { useEffect } from "react";
import Container from "@/components/shared/Container";
import Loading from "@/components/shared/Loading";

import reducer, {
  getDetails,
  useAppDispatch,
  useAppSelector,
  updateDetails,
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

injectReducer("updateSidesEdit", reducer);

const PortfolioEdit = () => {
  const dispatch = useAppDispatch();
  const query = useQuery();
  const navigate = useNavigate();

  useEffect(() => {
    fetchData();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const data = useAppSelector((state) => state.updateSidesEdit.data.portfolio);

  const loading = useAppSelector((state) => state.updateSidesEdit.data.loading);

  const fetchData = () => {
    const id = query.get("id");
    if (id) {
      dispatch(getDetails({ id }));
    }
  };

  const handleDiscard = () => {
    navigate("/settings/general/updateSide");
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
            t("settings.general.updateSide.successEdit"),
            t("settings.general.updateSide.edit"),
            "success"
          );
          navigate("/settings/general/updateSide");
        } else {
          ToastNotification(
            t("settings.general.updateSide.failEdit"),
            t("settings.general.updateSide.edit"),
            "danger"
          );
        }
      })
      .catch((error) => {
        ToastNotification(
          t("settings.general.updateSide.failEdit"),
          t("settings.general.updateSide.edit"),
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
            initialData={data}
            onDiscard={handleDiscard}
            onFormSubmit={handleFormSubmit}
          />
        )}
      </Loading>
    </Container>
  );
};

export default PortfolioEdit;
