import { useEffect, useState } from "react";
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
import {
  DroppedKeysDialog,
  findDroppedKeys,
  toCheckedKeys,
} from "../form/permissionSave";
import { requestPermissionRefresh } from "@/utils/v2/useSessionRefresh";

injectReducer("roleEdit", reducer);

const PortfolioEdit = () => {
  const dispatch = useAppDispatch();
  const query = useQuery();
  const navigate = useNavigate();
  const [dropped, setDropped] = useState<string[] | null>(null);

  useEffect(() => {
    fetchData();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const data = useAppSelector((state) => state.roleEdit.data.portfolio);

  const contractsOptions = useAppSelector(
    (state) => state.roleEdit.data.contractsOptions
  );

  const reachabilityOptions = useAppSelector(
    (state) => state.roleEdit.data.reachabilityOptions
  );

  const loading = useAppSelector((state) => state.roleEdit.data.loading);

  const fetchData = () => {
    const id = query.get("id");
    if (id) {
      dispatch(getDetails({ id }));
      dispatch(getContracts());
      dispatch(getCommentReachability());
    }
  };

  const handleDiscard = () => {
    navigate("/permissions");
  };

  const handleFormSubmit = async (
    values: FormModel,
    setSubmitting: SetSubmitting
  ) => {
    setSubmitting(true);
    const sent = toCheckedKeys(values as never);
    const success = await updateDetails({ ...values, permission: sent })
      .then(async (response) => {
        if (response.id) {
          /* Phase 3: if this is the signed-in user's own role, the nav follows at once. */
          requestPermissionRefresh();
          ToastNotification(
            t("permission.successEdit"),
            t("permission.edit"),
            "success"
          );
          // Read the role back: report any checked key the server did not keep.
          const missing = await findDroppedKeys(
            String((response as { id?: string }).id),
            sent
          ).catch(
            () => []
          );
          if (missing.length > 0) {
            setDropped(missing);
          } else {
            navigate("/permissions");
          }
        } else {
          ToastNotification(
            t("permission.failEdit"),
            t("permission.edit"),
            "danger"
          );
        }
      })
      .catch((error) => {
        ToastNotification(
          t("permission.failEdit"),
          t("permission.edit"),
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
            onFormSubmit={handleFormSubmit}
          />
        )}
      </Loading>
      <DroppedKeysDialog
        keys={dropped}
        onClose={() => {
          setDropped(null);
          navigate("/permissions");
        }}
      />
    </Container>
  );
};

export default PortfolioEdit;
