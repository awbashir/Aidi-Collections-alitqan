import { useEffect, useState } from "react";
import Container from "@/components/shared/Container";
import Loading from "@/components/shared/Loading";

import reducer, {
  useAppDispatch,
  useAppSelector,
  /*   getNationalities,
  getRoles,
  getBranches, */
  getContracts,
  getCommentReachability,
  addNew,
} from "./store";

import { injectReducer } from "@/store";
import isEmpty from "lodash/isEmpty";
import useQuery from "@/utils/hooks/useQuery";
import UserForm, { FormModel, SetSubmitting } from "../form/PortfolioForm";
import { useLocation, useNavigate } from "react-router-dom";
import toast from "@/components/ui/toast";
import Notification from "@/components/ui/Notification";
import ToastNotification from "@/components/shared/myComponents/ToastNotification";
import { useTranslation } from "react-i18next";
import {
  DroppedKeysDialog,
  findDroppedKeys,
  toCheckedKeys,
} from "../form/permissionSave";

injectReducer("roleNew", reducer);

const UserNew = () => {
  const dispatch = useAppDispatch();
  const navigate = useNavigate();
  const { t } = useTranslation();
  const [dropped, setDropped] = useState<string[] | null>(null);

  useEffect(() => {
    fetchData();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /* const naionalityOptions = useAppSelector(
    (state) => state.roleNew.data.nationalityOptions
  );

  const roleOptions = useAppSelector(
    (state) => state.roleNew.data.roleOptions
  );
  const branchOptions = useAppSelector(
    (state) => state.roleNew.data.branchOptions
  ); */

  const contractsOptions = useAppSelector(
    (state) => state.roleNew.data.contractsOptions
  );

  const reachabilityOptions = useAppSelector(
    (state) => state.roleNew.data.reachabilityOptions
  );

  const loading = useAppSelector((state) => state.roleNew.data.loading);

  const fetchData = () => {
    /*  dispatch(getNationalities());
    dispatch(getRoles());
    dispatch(getBranches()); */
    dispatch(getContracts());
    dispatch(getCommentReachability());
  };

  const handleDiscard = () => {
    navigate("/permissions");
  };

  const handleFormSubmit = async (
    values: FormModel,
    setSubmitting: SetSubmitting
  ) => {
    setSubmitting(true);
    //delete id from values
    delete values.id;
    const sent = toCheckedKeys(values as never);
    const success = await addNew({ ...values, permission: sent })
      .then(async (response) => {
        if (response.id) {
          ToastNotification(
            t("permission.successAdd"),
            t("permission.new"),
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
            t("permission.failAdd"),
            t("permission.new"),
            "danger"
          );
        }
      })
      .catch((error) => {
        ToastNotification(
          t("permission.failAdd"),
          t("permission.new"),
          "danger"
        );
      });

    setSubmitting(false);
  };

  return (
    <Container className="h-full">
      <UserForm
        type="New"
        /*  nationalities={naionalityOptions}
        roles={roleOptions}
        branches={branchOptions} */
        contracts={contractsOptions}
        reachability={reachabilityOptions}
        onDiscard={handleDiscard}
        onFormSubmit={handleFormSubmit}
      />
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

export default UserNew;
