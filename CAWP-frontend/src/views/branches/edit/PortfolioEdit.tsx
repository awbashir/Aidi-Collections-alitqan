import { useEffect } from "react";
import Container from "@/components/shared/Container";
import Loading from "@/components/shared/Loading";

import reducer, {
  getPortfolio,
  useAppDispatch,
  useAppSelector,
  updatePortfolio,
} from "./store";

import { injectReducer } from "@/store";
import isEmpty from "lodash/isEmpty";
import useQuery from "@/utils/hooks/useQuery";
import PortfolioForm, { FormModel, SetSubmitting } from "../form/PortfolioForm";
import { useLocation, useNavigate } from "react-router-dom";
import toast from "@/components/ui/toast";
import Notification from "@/components/ui/Notification";
import { t } from "i18next";

injectReducer("BranchesEdit", reducer);

const BranchesEdit = () => {
  const dispatch = useAppDispatch();
  const query = useQuery();
  const navigate = useNavigate();

  useEffect(() => {
    fetchData();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const data = useAppSelector((state) => state.BranchesEdit.data.portfolio);

  const loading = useAppSelector((state) => state.BranchesEdit.data.loading);

  const fetchData = () => {
    const id = query.get("id");
    if (id) {
      dispatch(getPortfolio({ id }));
    }
  };

  const handleDiscard = () => {
    navigate("/branch");
  };

  const handleFormSubmit = async (
    values: FormModel,
    setSubmitting: SetSubmitting
  ) => {
    setSubmitting(true);
    const success = await updatePortfolio(values);
    setSubmitting(false);
    if (success) {
      popNotification("editSuccess");
    }
  };

  const popNotification = (keyword: string) => {
    toast.push(
      <Notification type="success" duration={2500}>
        {t(`portfolios.${keyword}`)}
      </Notification>,
      {
        placement: "top-center",
      }
    );
    navigate("/branch");
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

export default BranchesEdit;
