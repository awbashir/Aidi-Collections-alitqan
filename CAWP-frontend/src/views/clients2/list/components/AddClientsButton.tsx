import axios from "axios";
import { useContext } from "react";
import { useState } from "react";
import { useEffect } from "react";
import { Formik, Field } from "formik";
import { FormItem, FormContainer } from "@/components/ui/Form";
import * as Yup from "yup";
import Button from "@/components/ui/Button";
import Dialog from "@/components/ui/Dialog";
import Select from "@/components/ui/Select";
import { useTranslation } from "react-i18next";
import Upload from "@/components/ui/Upload";
import {
  apiUploadFile,
  apiPostClintAddClintsByExcel,
} from "@/services/ClientsService";
import ToastNotification from "@/components/shared/myComponents/ToastNotification";
import { HiOutlineCloudUpload, HiPlusCircle } from "react-icons/hi";
import Input from "@/components/ui/Input";
import { useAppSelector, useAppDispatch, getContracts } from "../store";
import Loading from "@/components/shared/Loading";
import JobProgress from "@/components/shared/JobProgress";
import useExcelImportJob from "@/utils/hooks/useExcelImportJob";

function AddClientsByExcelButton({
  filter,
  notificationTitle,
  notificationSuccessMessage,
  notificationFailedMessage,
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const handleClose = () => setOpen(false);
  const [isLoading, setIsLoading] = useState(false);
  const [columnsOptions, setColumnsOptions] = useState([]);

  const [columnsByExcel, setColumnsByExcel] = useState([]);
  const [dataByExcel, setDataByExcel] = useState([]);
  const [inquiryClintByExcel, setInquiryClintByExcel] = useState([]);

  const handleOpen = (e, id) => {
    setOpen(true);
  };

  const handleChangeByExcel = async (event) => {
    const formData = new FormData();
    setIsLoading(true);
    formData.append("file", event[0]);
    formData.append("fileName", event[0].name);
    await apiUploadFile(formData)
      .then((response) => {
        if (response.data.status) {
          setColumnsByExcel(response.data.data);
          setDataByExcel(response.data.excel_data);
          ToastNotification(
            t(notificationSuccessMessage),
            t(notificationTitle),
            "success"
          );
          setIsLoading(false);
        } else {
          ToastNotification(
            t(notificationFailedMessage),
            t(notificationTitle),
            "danger"
          );
        }
      })
      .catch((error) => {
        ToastNotification(
          t(notificationFailedMessage),
          t(notificationTitle),
          "danger"
        );
      });
  };

  useEffect(() => {
    let w = columnsByExcel.map((member) => ({
      value: member.header,
      label: member.header,
    }));
    setColumnsOptions(w);
  }, [columnsByExcel]);

  const dispatch = useAppDispatch();

  const { contracts } = useAppSelector((state) => state.clientsList2.data);

  const optionsContract = contracts?.map((contract) => ({
    label: contract.name,
    value: contract.id,
  }));

  useEffect(() => {
    if (open) {
      dispatch(getContracts());
    }
  }, [open]);

  const Schema = Yup.object().shape({
    contract_id: Yup.string().required(t("requiredField")),
    name: Yup.string().required(t("requiredField")),
  });

  const initialValues = {
    contract_id: "",
    name: "",
  };

  /* The one place a submit body becomes UI. A background job's `result` is
   * byte-identical to the synchronous response, so both transports land here;
   * `fromJob` only prefixes the toast with "اكتمل". */
  const handleResult = (body, fromJob = false) => {
    const prefix = fromJob ? `${t("job.completed")}: ` : "";
    if (body?.status) {
      ToastNotification(
        prefix +
          (fromJob && body?.message
            ? body.message
            : t("clients.addByExcelSuccess")),
        t("clients.addByExcel"),
        "success"
      );
      setIsLoading(false);
    } else {
      ToastNotification(
        prefix +
          (fromJob && body?.message
            ? body.message
            : t("clients.addByExcelFail")),
        t("clients.addByExcel"),
        "danger"
      );
    }
    handleClose();
  };

  const {
    jobId,
    job,
    error: jobError,
    expired: jobExpired,
    isRunning: jobRunning,
    isDone: jobDone,
    isFailed: jobFailed,
    handleResponse: handleImportResponse,
    reset: resetImportJob,
  } = useExcelImportJob(handleResult);

  const handelSubmitForm = async (values) => {
    var sendvalue = values;
    sendvalue.data = dataByExcel;

    setIsLoading(true);
    resetImportJob();

    await apiPostClintAddClintsByExcel(sendvalue, { async: true })
      .then((response) => {
        /* 202 -> the rows are queued: keep the dialog open on JobProgress.
         * Anything else is the final body and goes straight to handleResult. */
        if (handleImportResponse(response)) {
          setIsLoading(false);
        }
      })
      .catch((error) => {
        ToastNotification(
          t("archive.failMoveFromArchive"),
          t("archive.moveFromArchive"),
          "danger"
        );
        handleClose();
      });
  };

  return (
    <div>
      <div>
        <Button
          variant="solid"
          size="sm"
          icon={<HiPlusCircle />}
          onClick={handleOpen}
        >
          {t("clients.adds")}
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
            <h5 className="pb-4 text-[16px] font-extrabold text-navy-600 dark:text-night-body">{t("clients.addByExcel")}</h5>
            {jobId && (
              <JobProgress
                className="mb-4"
                job={job}
                error={jobError}
                expired={jobExpired}
                isDone={jobDone}
                isFailed={jobFailed}
              />
            )}


            <span>
              <Loading loading={isLoading} type="cover">
                <Formik
                  onSubmit={handelSubmitForm}
                  initialValues={initialValues}
                  validationSchema={Schema}
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
                        <div class="grid">
                          <Upload
                            type="file"
                            accept="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                            onChange={handleChangeByExcel}
                          >
                            <Button
                              variant="twoTone"
                              icon={<HiOutlineCloudUpload />}
                              type="button"
                              size="sm"
                            >
                              {t("uploadExcel")}
                            </Button>
                          </Upload>
                        </div>

                        <div class="grid" item xs={12} md={12}>
                          {/*  <FormItem
                          className="col-span"
                          label={t("clients.accountNumber")}
                          invalid={
                            errors.account_number && touched.account_number
                          }
                          errorMessage={errors.account_number}
                        >
                          <Field name="account_number">
                            {({ field, form }: FieldProps) => {
                              return (
                                <Select
                                  field={field}
                                  form={form}
                                  options={columnsOptions}
                                  placeholder={t("select")}
                                  noOptionsMessage={() => t("noOptionsMessage")}
                                  value={
                                    columnsOptions.find(
                                      (option) => option.value === field.value
                                    ) || ""
                                  }
                                  onChange={(option) => {
                                    form.setFieldValue(
                                      field.name,
                                      option.value
                                    );
                                  }}
                                />
                              );
                            }}
                          </Field>
                        </FormItem> */}

                          <FormItem
                            asterisk
                            label={t("contracts.title")}
                            invalid={errors.contract_id && touched.contract_id}
                            errorMessage={errors.contract_id}
                          >
                            <Field name="contract_id">
                              {({ field, form }: FieldProps<FormModel>) => (
                                <Select
                                  placeholder={t("select")}
                                  field={field}
                                  form={form}
                                  options={optionsContract}
                                  value={optionsContract?.filter(
                                    (option) =>
                                      option.value === values.contract_id
                                  )}
                                  onChange={(option) =>
                                    form.setFieldValue(
                                      field.name,
                                      option?.value
                                    )
                                  }
                                />
                              )}
                            </Field>
                          </FormItem>

                          <FormItem
                            asterisk
                            label={t("portfolios.name")}
                            invalid={errors.name && touched.name}
                            errorMessage={errors.name}
                          >
                            <Field
                              name="name"
                              type="text"
                              autoComplete="off"
                              placeholder={t("portfolios.name")}
                              component={Input}
                            />
                          </FormItem>
                        </div>

                        <div class="my-5" item xs={12} md={12}>
                          <div className="mt-4 flex flex-wrap items-center justify-end gap-2">
                            <div className="flex flex-wrap items-center gap-2">
                              <Button
                                variant="solid"
                                type="submit"
                                size="sm"
                                disabled={jobRunning}
                              >
                                {t("add")}
                              </Button>
                              <Button
                                size="sm"
                                variant="default"
                                onClick={handleClose}
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
              </Loading>
            </span>
          </div>
        </Dialog>
      </div>
    </div>
  );
}

export default AddClientsByExcelButton;
