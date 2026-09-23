import { useEffect } from "react";
import Container from "@/components/shared/Container";
import Loading from "@/components/shared/Loading";

import reducer, {
  getUser,
  useAppDispatch,
  useAppSelector,
  getNationalities,
  getRoles,
  getContracts,
  getBranches,
  updateUser,
} from "./store";

import { injectReducer } from "@/store";
import isEmpty from "lodash/isEmpty";
import useQuery from "@/utils/hooks/useQuery";
import UserForm, { FormModel, SetSubmitting } from "../form/UserForm";
import { useLocation, useNavigate } from "react-router-dom";
import toast from "@/components/ui/toast";
import Notification from "@/components/ui/Notification";

injectReducer("userEdit", reducer);

const UserEdit = () => {
  const dispatch = useAppDispatch();
  const query = useQuery();
  const navigate = useNavigate();

  useEffect(() => {
    fetchData();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const data = useAppSelector((state) => state.userEdit.data.user);
  const naionalityOptions = useAppSelector(
    (state) => state.userEdit.data.nationalityOptions
  );

  const roleOptions = useAppSelector(
    (state) => state.userEdit.data.roleOptions
  );
  const branchOptions = useAppSelector(
    (state) => state.userEdit.data.branchOptions
  );

  const contractOptions = useAppSelector(
    (state) => state.userEdit.data.contractOptions
  );
  const loading = useAppSelector((state) => state.userEdit.data.loading);

  const fetchData = () => {
    const id = query.get("id");
    if (id) {
      dispatch(getUser({ id }));
      dispatch(getNationalities());
      dispatch(getRoles());
      dispatch(getBranches());
      dispatch(getContracts());
    }
  };

  const handleDiscard = () => {
    navigate("/users");
  };

  const handleFormSubmit = async (
    values: FormModel,
    setSubmitting: SetSubmitting
  ) => {
    setSubmitting(true);
    const success = await updateUser(values);
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
    navigate("/users");
  };

  return (
    <Container className="h-full">
      <Loading loading={loading}>
        {!isEmpty(data) && (
          <UserForm
            type="edit"
            initialData={data}
            nationalities={naionalityOptions}
            roles={roleOptions}
            branches={branchOptions}
            contracts={contractOptions}
            onDiscard={handleDiscard}
            onFormSubmit={handleFormSubmit}
          />
        )}
      </Loading>
    </Container>
  );
};

export default UserEdit;
