import { useEffect } from "react";
import Container from "@/components/shared/Container";
import Loading from "@/components/shared/Loading";

import reducer, {
  useAppDispatch,
  useAppSelector,
  /*   getNationalities,
  getRoles,
  getBranches, */
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

injectReducer("ArchiveReasonNew", reducer);

const UserNew = () => {
  const dispatch = useAppDispatch();
  const navigate = useNavigate();
  const { t } = useTranslation();

  useEffect(() => {
    fetchData();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const naionalityOptions = useAppSelector(
    (state) => state.ArchiveReasonNew.data.nationalityOptions
  );

  const roleOptions = useAppSelector(
    (state) => state.ArchiveReasonNew.data.roleOptions
  );
  const branchOptions = useAppSelector(
    (state) => state.ArchiveReasonNew.data.branchOptions
  );
  const loading = useAppSelector(
    (state) => state.ArchiveReasonNew.data.loading
  );

  const fetchData = () => {
    /*  dispatch(getNationalities());
    dispatch(getRoles());
    dispatch(getBranches()); */
  };

  const handleDiscard = () => {
    navigate("/settings/general/archiveReasons");
  };

  const handleFormSubmit = async (
    values: FormModel,
    setSubmitting: SetSubmitting
  ) => {
    setSubmitting(true);
    //delete id from values
    delete values.id;
    delete values.is_active;
    const success = await addNew(values)
      .then((response) => {
        if (response.id) {
          ToastNotification(
            t("settings.general.archiveReasons.successAdd"),
            t("settings.general.archiveReasons.new"),
            "success"
          );
          navigate("/settings/general/archiveReasons");
        } else {
          ToastNotification(
            t("settings.general.archiveReasons.failAdd"),
            t("settings.general.archiveReasons.new"),
            "danger"
          );
        }
      })
      .catch((error) => {
        ToastNotification(
          t("settings.general.archiveReasons.failAdd"),
          t("settings.general.archiveReasons.new"),
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
        onDiscard={handleDiscard}
        onFormSubmit={handleFormSubmit}
      />
    </Container>
  );
};

export default UserNew;
