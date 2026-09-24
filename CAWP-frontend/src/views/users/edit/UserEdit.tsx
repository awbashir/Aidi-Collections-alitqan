import { useEffect, useState } from "react";
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
// Phase 3: target history + v2 target editing under the legacy form
import TargetsPanel from "@/views/phase3/targets/TargetsPanel";
import { usePermissions } from "@/utils/v2/hooks";

injectReducer("userEdit", reducer);

// Phase 3: fields the API returns as null to a viewer without the permission to see them
const PROTECTED_USER_FIELDS = [
  "identityNumber",
  "mobile",
  "email",
  "target_amount",
  "target_percent",
  "target_is_percentage",
] as const;

const UserEdit = () => {
  const dispatch = useAppDispatch();
  const query = useQuery();
  const navigate = useNavigate();
  // Phase 3: bumped after a v2 target save mirrored into the legacy columns, so the form remounts with them
  const [formKey, setFormKey] = useState(0);
  const perms = usePermissions();
  const userId = query.get("id");

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
    // Phase 3: protected employee fields come back as null when the viewer may not see them.
    // A key sent as null would overwrite the stored value (PUT /uesrs/<id> assigns every key
    // present, and its field gate compares null as "changed"), so a field that was hidden
    // (null) on load and is still empty is left out of the payload: a missing key = unchanged.
    const payload: Record<string, unknown> = { ...values };
    PROTECTED_USER_FIELDS.forEach((field) => {
      const loaded = (data as Record<string, unknown> | undefined)?.[field];
      const current = payload[field];
      if (loaded === null && (current === null || current === undefined || current === "")) {
        delete payload[field];
      }
    });
    const success = await updateUser(payload);
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
            key={formKey}
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
      {/* Phase 3: target history (read: users_edit_target | users_details; the panel hides its form without users_edit_target) */}
      {userId && (perms.users_edit_target || perms.users_details) && (
        <TargetsPanel
          subject="user"
          subjectId={userId}
          onMirrored={() => {
            dispatch(getUser({ id: userId }));
            setFormKey((k) => k + 1);
          }}
        />
      )}
    </Container>
  );
};

export default UserEdit;
