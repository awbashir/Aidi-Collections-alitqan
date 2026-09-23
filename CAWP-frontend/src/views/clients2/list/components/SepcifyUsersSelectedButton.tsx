import axios from "axios";
import { useContext, useCallback, useEffect, useState } from "react";
import { Formik, Field } from "formik";
import { FormItem, FormContainer } from "@/components/ui/Form";
import * as Yup from "yup";
import Button from "@/components/ui/Button";
import Dialog from "@/components/ui/Dialog";
import Select from "@/components/ui/Select";
import { useTranslation } from "react-i18next";
import Upload from "@/components/ui/Upload";
import { apiPostSpecifyUsersIdsOne } from "@/services/ClientsService";
import ToastNotification from "@/components/shared/myComponents/ToastNotification";
import { HiOutlineCloudUpload, HiOutlineUserAdd } from "react-icons/hi";
import Tooltip from "@/components/ui/Tooltip";
import useThemeClass from "@/utils/hooks/useThemeClass";
import { HiOutlineUser } from "react-icons/hi";
import { useFetchClients } from "../hooks/GetClientHook";

import {
  useAppSelector,
  useAppDispatch,
  getBranches,
  getUsers,
} from "../store";

function SepcifyUsersSelectedButton({ ids }) {
  const { t } = useTranslation();
  const { textTheme } = useThemeClass();
  const dispatch = useAppDispatch();
  const fetchData = useFetchClients();
  const [open, setOpen] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const handleClose = () => setOpen(false);

  const handleOpen = (e, id) => {
    setOpen(true);
  };

  //-------------------------------------

  const userId = useAppSelector((state) => state.auth.user.id);

  const handelInquiryClint = async (values) => {
    var sendvalue = values;

    await apiPostSpecifyUsersIdsOne(sendvalue)
      .then((response) => {
        if (response.status) {
          ToastNotification(
            t("clients.SepcifyClientsToUserSuccess"),
            t("clients.SepcifyClientsToUser"),
            "success"
          );
          fetchData();
        } else {
          ToastNotification(
            t("clients.SepcifyClientsToUserFail"),
            t("clients.SepcifyClientsToUser"),
            "danger"
          );
        }
      })
      .catch((error) => {
        ToastNotification(
          t("clients.SepcifyClientsToUserFail"),
          t("clients.SepcifyClientsToUser"),
          "danger"
        );
      });

    handleClose();
  };

  const initialValues = {
    user_id: "",
    clint_ids: ids,
  };

  const SchemaArchiveUser = Yup.object().shape({
    user_id: Yup.string().required(t("requiredField")),
    clint_ids: Yup.array().min(1).required(t("requiredField")),
  });

  const { branches, users } = useAppSelector((state) => state.clientsList2.data);

  const options = branches?.map((reason) => ({
    label: reason.name,
    value: reason.id,
  }));

  const optionsUsers = users?.map((user) => ({
    label: user.name,
    value: user.id,
    branch_id: user.branch_id,
  }));
  useEffect(() => {
    if (open) {
      dispatch(getBranches());
      dispatch(getUsers());
    }
  }, [open]);

  //--------------------------------------

  return (
    <div>
      <div>
        <Button
          variant="default"
          loading={isLoading}
          onClick={handleOpen}
          size="sm"
          icon={<HiOutlineUserAdd />}
        >
          {t("SepcifyUser")}
        </Button>

        <Dialog
          isOpen={open}
          closable={false}
          /*  open={open}
          onClose={handleClose}
          aria-labelledby="modal-modal-title"
          aria-describedby="modal-modal-description" */
        >
          <div>
            <h5 className="pb-4 text-[16px] font-extrabold text-navy-600 dark:text-night-body">{t("clients.SepcifyClientToUser")}</h5>

            <span>
              <Formik
                onSubmit={handelInquiryClint}
                initialValues={initialValues}
                validationSchema={SchemaArchiveUser}
              >
                {({
                  values,
                  errors,
                  touched,
                  handleChange,
                  handleBlur,
                  handleSubmit,
                }) => (
                  <form onSubmit={handleSubmit}>
                    <div class="grid" container spacing={2}>
                      <div class="grid" item xs={12} md={12}>
                        {/*  <FormItem
                          asterisk
                          label={t("ArchiveReason")}
                          invalid={
                            errors.archive_reason_id &&
                            touched.archive_reason_id
                          }
                          errorMessage={errors.archive_reason_id}
                        >
                          <Field name="archive_reason_id">
                            {({ field, form }: FieldProps<FormModel>) => (
                              <Select
                                placeholder={t("select")}
                                field={field}
                                form={form}
                                options={options}
                                value={options?.filter(
                                  (option) =>
                                    option.value === values.archive_reason_id
                                )}
                                onChange={(option) =>
                                  form.setFieldValue(field.name, option?.value)
                                }
                              />
                            )}
                          </Field>
                        </FormItem> */}

                        <FormItem
                          asterisk
                          label={t("branch")}
                          invalid={errors.branch_id && touched.branch_id}
                          errorMessage={errors.branch_id}
                        >
                          <Field name="branch_id">
                            {({ field, form }: FieldProps<FormModel>) => (
                              <Select
                                placeholder={t("select")}
                                field={field}
                                form={form}
                                options={options}
                                value={options?.filter(
                                  (option) => option.value === values.branch_id
                                )}
                                onChange={(option) =>
                                  form.setFieldValue(field.name, option?.value)
                                }
                              />
                            )}
                          </Field>
                        </FormItem>

                        <FormItem
                          asterisk
                          label={t("users.name")}
                          invalid={errors.user_id && touched.user_id}
                          errorMessage={errors.user_id}
                        >
                          <Field name="user_id">
                            {({ field, form }: FieldProps<FormModel>) => (
                              <Select
                                placeholder={t("select")}
                                field={field}
                                form={form}
                                options={optionsUsers?.filter(
                                  (option) =>
                                    option.branch_id === values.branch_id
                                )}
                                value={optionsUsers?.filter(
                                  (option) => option.value === values.user_id
                                )}
                                onChange={(option) =>
                                  form.setFieldValue(field.name, option?.value)
                                }
                              />
                            )}
                          </Field>
                        </FormItem>
                      </div>

                      <div class="my-5" item xs={12} md={12}>
                        <div className="mt-4 flex flex-wrap items-center justify-end gap-2">
                          <div className="flex flex-wrap items-center gap-2">
                            <Button
                              size="sm"
                              className="ltr:mr-2 rtl:ml-2"
                              variant="solid"
                              type="submit"
                            >
                              {t("save")}
                            </Button>
                            <Button
                              size="sm"
                              variant="default"
                              onClick={handleClose}
                              type="button"
                            >
                              {t("cancel")}
                            </Button>
                          </div>
                        </div>
                      </div>
                    </div>
                  </form>
                )}
              </Formik>
            </span>
          </div>
        </Dialog>
      </div>
    </div>
  );
}

export default SepcifyUsersSelectedButton;
