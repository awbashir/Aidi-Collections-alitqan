import { useEffect } from "react";
import Container from "@/components/shared/Container";
import Loading from "@/components/shared/Loading";

import reducer, {
  getPortfolio,
  useAppDispatch,
  useAppSelector,
  updatePortfolio,
  getBranches,
  getUsers,
  getContracts,
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

injectReducer("supervisorEdit", reducer);

const SupervisorEdit = () => {
  const dispatch = useAppDispatch();
  const query = useQuery();
  const navigate = useNavigate();

  useEffect(() => {
    fetchData();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const data = useAppSelector((state) => state.supervisorEdit.data.portfolio);

  const loading = useAppSelector((state) => state.supervisorEdit.data.loading);

  const branchOptions = useAppSelector(
    (state) => state.supervisorEdit.data.branchOptions,
  );

  const userOptions = useAppSelector(
    (state) => state.supervisorEdit.data.userOptions,
  );
  const contractOtions = useAppSelector(
    (state) => state.supervisorEdit.data.contractOptions,
  );

  const fetchData = () => {
    const id = query.get("id");
    if (id) {
      dispatch(getPortfolio({ id }));
      dispatch(getBranches());
      dispatch(getUsers());
      dispatch(getContracts());
    }
  };

  const handleDiscard = () => {
    navigate("/supervisor");
  };

  const handleFormSubmit = async (
    values: FormModel,
    setSubmitting: SetSubmitting,
  ) => {
    setSubmitting(true);

    //--------------------------------------------------
    // get contracta-result contracts ids
    const supervisor_id = query.get("id");
    let contracts_ids = [];
    values.contract_result.map((contract) =>
      contracts_ids.push(contract.value),
    );

    // get user-result users ids
    let users_ids = [];
    values.user_result.map((user) => users_ids.push(user.value));

    const data = {
      user_id: values.user_id,
      contracts_ids: contracts_ids,
      users_ids: users_ids,
      supervisor_id: supervisor_id,
    };

    //---------------------------------------------

    const success = await updatePortfolio(data);
    setSubmitting(false);
    if (success) {
      ToastNotification(
        t("supervisors.editSuccess"),
        t("supervisors.edit"),
        "success",
      );

      navigate("/supervisor");
    }
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
            branches={branchOptions}
            users={userOptions}
            contracts={contractOtions}
          />
        )}
      </Loading>
    </Container>
  );
};

export default SupervisorEdit;
