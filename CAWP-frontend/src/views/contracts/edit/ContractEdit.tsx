import { useEffect } from "react";
import Container from "@/components/shared/Container";
import Loading from "@/components/shared/Loading";

import reducer, {
  getContract,
  useAppDispatch,
  useAppSelector,
  updateContract,
} from "./store";

import { injectReducer } from "@/store";
import isEmpty from "lodash/isEmpty";
import useQuery from "@/utils/hooks/useQuery";
import ContractForm, { FormModel, SetSubmitting } from "../form/ContractForm";
import { useLocation, useNavigate } from "react-router-dom";
import toast from "@/components/ui/toast";
import Notification from "@/components/ui/Notification";

injectReducer("contractEdit", reducer);

const ContractEdit = () => {
  const dispatch = useAppDispatch();
  const query = useQuery();
  const navigate = useNavigate();

  useEffect(() => {
    fetchData();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const data = useAppSelector((state) => state.contractEdit.data.contract);

  const loading = useAppSelector((state) => state.contractEdit.data.loading);

  const fetchData = () => {
    const id = query.get("id");
    if (id) {
      dispatch(getContract({ id }));
    }
  };

  const handleDiscard = () => {
    navigate("/contracts");
  };

  const handleFormSubmit = async (
    values: FormModel,
    setSubmitting: SetSubmitting
  ) => {
    setSubmitting(true);
    const success = await updateContract(values);
    setSubmitting(false);
    if (success) {
      popNotification("updated");
    }
  };

  const popNotification = (keyword: string) => {
    toast.push(
      <Notification
        title={`Successfuly ${keyword}`}
        type="success"
        duration={2500}
      >
        Product successfuly {keyword}
      </Notification>,
      {
        placement: "top-center",
      }
    );
    navigate("/contracts");
  };

  return (
    <Container className="h-full">
      <Loading loading={loading}>
        {!isEmpty(data) && (
          <ContractForm
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

export default ContractEdit;
