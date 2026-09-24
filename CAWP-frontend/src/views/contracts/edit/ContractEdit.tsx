import { useEffect, useState } from "react";
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
// Phase 3: target history + v2 target editing under the legacy form
import TargetsPanel from "@/views/phase3/targets/TargetsPanel";
import { usePermissions } from "@/utils/v2/hooks";

injectReducer("contractEdit", reducer);

const ContractEdit = () => {
  const dispatch = useAppDispatch();
  const query = useQuery();
  const navigate = useNavigate();
  // Phase 3: bumped after a v2 target save mirrored into the legacy columns, so the form remounts with them
  const [formKey, setFormKey] = useState(0);
  const perms = usePermissions();
  const contractId = query.get("id");

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
            key={formKey}
            type="edit"
            initialData={data}
            onDiscard={handleDiscard}
            onFormSubmit={handleFormSubmit}
          />
        )}
      </Loading>
      {/* Phase 3: target history (read: contracts | contracts_edit; the panel hides its form without contracts_edit) */}
      {contractId && (perms.contracts || perms.contracts_edit) && (
        <TargetsPanel
          subject="contract"
          subjectId={contractId}
          onMirrored={() => {
            dispatch(getContract({ id: contractId }));
            setFormKey((k) => k + 1);
          }}
        />
      )}
    </Container>
  );
};

export default ContractEdit;
