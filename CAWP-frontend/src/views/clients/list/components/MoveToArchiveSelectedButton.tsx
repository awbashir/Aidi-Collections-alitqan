import axios from "axios";
import { useContext, useCallback, useState, useEffect } from "react";
import { Formik, Field } from "formik";
import { FormItem, FormContainer } from "@/components/ui/Form";
import * as Yup from "yup";
import Button from "@/components/ui/Button";
import Dialog from "@/components/ui/Dialog";
import Select from "@/components/ui/Select";
import { useTranslation } from "react-i18next";
import Upload from "@/components/ui/Upload";
import { apiPutClintArchiveByIDs } from "@/services/ClientsService";
import ToastNotification from "@/components/shared/myComponents/ToastNotification";
import { HiOutlineCloudUpload } from "react-icons/hi";
import Tooltip from "@/components/ui/Tooltip";
import useThemeClass from "@/utils/hooks/useThemeClass";
import { HiOutlineArchive } from "react-icons/hi";
import { useFetchClients } from "../hooks/GetClientHook";

import {
  useAppSelector,
  useAppDispatch,
  getArchiveReasons,
  getClients,
} from "../store";

function MoveToArchiveSelectedButton({ ids }) {
  const { t } = useTranslation();
  const { textTheme } = useThemeClass();
  const fetchData = useFetchClients();

  const [open, setOpen] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const handleClose = () => setOpen(false);

  const [columnsByExcel, setColumnsByExcel] = useState([]);
  const [dataByExcel, setDataByExcel] = useState([]);
  const [inquiryClintByExcel, setInquiryClintByExcel] = useState([]);
  const dispatch = useAppDispatch();

  const handleOpen = (e, id) => {
    setOpen(true);
  };

  //-------------------------------------

  const userId = useAppSelector((state) => state.auth.user.id);

  const handelInquiryClint = async (values) => {
    var sendvalue = values;

    await apiPutClintArchiveByIDs(sendvalue)
      .then((response) => {
        if (response.data.status) {
          fetchData();
          ToastNotification(
            t("clients.archiveClintsSuccess"),
            t("clients.archive"),
            "success"
          );
        } else {
          ToastNotification(
            t("clients.archiveClintsFail"),
            t("clients.archive"),
            "danger"
          );
        }
      })
      .catch((error) => {
        ToastNotification(
          t("clients.archiveClintsFail"),
          t("clients.archive"),
          "danger"
        );
      });

    handleClose();
  };

  const initialValues = {
    archive_reason_id: "",
    user_id: userId,
    clint_ids: ids,
  };

  const SchemaArchiveUser = Yup.object().shape({
    archive_reason_id: Yup.string().required(t("requiredField")),
  });

  const { archiveReasons } = useAppSelector((state) => state.clientsList.data);
  const options = archiveReasons?.map((reason) => ({
    label: reason.name,
    value: reason.id,
  }));
  useEffect(() => {
    if (open) {
      dispatch(getArchiveReasons());
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
          icon={<HiOutlineArchive />}
        >
          {t("moveArchive")}
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
            <h5 className="py-5">{t("clients.confirmArchiveClints")}</h5>

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
                        <FormItem
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
                        </FormItem>
                      </div>

                      <div class="my-5" item xs={12} md={12}>
                        <div class="mt-4 ltr:text-right">
                          <div class="stack" direction="row" spacing={2}>
                            <Button
                              className="ltr:mr-2 rtl:ml-2"
                              variant="solid"
                              type="submit"
                            >
                              {t("move")}
                            </Button>
                            <Button
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

export default MoveToArchiveSelectedButton;
