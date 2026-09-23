import { useEffect } from "react";
import Container from "@/components/shared/Container";
import Loading from "@/components/shared/Loading";

import reducer, {
  getClient,
  useAppDispatch,
  useAppSelector,
  updateClient,
} from "./store";

import { injectReducer } from "@/store";
import isEmpty from "lodash/isEmpty";
import useQuery from "@/utils/hooks/useQuery";
import ClientForm, { FormModel, SetSubmitting } from "../form/ClientForm";
import { useLocation, useNavigate } from "react-router-dom";
import toast from "@/components/ui/toast";
import Notification from "@/components/ui/Notification";
import { t } from "i18next";

injectReducer("clientEdit", reducer);

const ClientEdit = () => {
  const dispatch = useAppDispatch();
  const query = useQuery();
  const navigate = useNavigate();

  useEffect(() => {
    fetchData();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const data = useAppSelector((state) => state.clientEdit.data.client);

  const loading = useAppSelector((state) => state.clientEdit.data.loading);

  const fetchData = () => {
    const id = query.get("id");
    if (id) {
      dispatch(getClient({ id }));
    }
  };

  const handleDiscard = () => {
    navigate("/clients");
  };

  const handleFormSubmit = async (
    values: FormModel,
    setSubmitting: SetSubmitting
  ) => {
    setSubmitting(true);
    const success = await updateClient(values);
    setSubmitting(false);
    if (success) {
      popNotification("editSuccess");
    }
  };

  const popNotification = (keyword: string) => {
    toast.push(
      <Notification type="success" duration={2500}>
        {t(`clients.${keyword}`)}
      </Notification>,
      {
        placement: "top-center",
      }
    );
    navigate("/clients");
  };

  return (
    <Container className="h-full">
      <Loading loading={loading}>
        {!isEmpty(data) && (
          <ClientForm
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

export default ClientEdit;
