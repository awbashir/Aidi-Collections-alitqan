import { useEffect } from "react";
import Container from "@/components/shared/Container";
import Loading from "@/components/shared/Loading";

import reducer, {
  useAppDispatch,
  useAppSelector,
  /*  getNationalities,
  getRoles, */
  getBranches,
  getUsers,
  getContracts,
  newSupervior,
  newSuperviorContract,
  newSuperviorUser,
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

  const userOptions = useAppSelector((state) => state.userNew.data.userOptions);
  const contractOtions = useAppSelector(
    (state) => state.userNew.data.contractOptions
  );
  const loading = useAppSelector((state) => state.userNew.data.loading);
  const { t } = useTranslation();

  const fetchData = () => {
    // dispatch(getNationalities());
    // dispatch(getRoles());
    dispatch(getBranches());
    dispatch(getUsers());
    dispatch(getContracts());
  };

  const handleDiscard = () => {
    navigate("/supervisor");
  };

  const handleFormSubmit = async (
    values: FormModel,
    setSubmitting: SetSubmitting
  ) => {
    setSubmitting(true);
    //delete id from values
    delete values.id;
    //------------------------------------------------------------

    //--------------------------------------------------
    // get contracta-result contracts id
    var contracts_id = [];
    values?.contract_result.map((item) => contracts_id.push(item.value));

    // get user-result users id
    var users_id = [];
    values?.user_result.map((item) => users_id.push(item.value));

    //---------------------------------------------

    var supervisor_value = { user_id: values.user_id };
    const { id } = await newSupervior(supervisor_value);
    var supervisor_id = id;

    if (supervisor_id) {
      var contract_value = {
        supervisor_id: supervisor_id,
        contract_id: contracts_id,
      };

      var user_value = {
        supervisor_id: supervisor_id,
        users: users_id,
      };
      if (contracts_id.length > 0) {
        await newSuperviorContract(contract_value);
      }
      if (users_id.length > 0) {
        await newSuperviorUser(user_value);
      }
    }

    ToastNotification(
      t("supervisors.addSuccess"),
      t("supervisors.add"),
      "success"
    );
    navigate("/supervisor");
    setSubmitting(false);

    //--------------------------------------------------------------
  };

  return (
    <Container className="h-full">
      <PortfolioForm
        type="New"
        /*  nationalities={naionalityOptions}
        roles={roleOptions} */
        branches={branchOptions}
        users={userOptions}
        contracts={contractOtions}
        onDiscard={handleDiscard}
        onFormSubmit={handleFormSubmit}
      />
    </Container>
  );
};

export default UserNew;
