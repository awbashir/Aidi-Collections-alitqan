import { useState } from "react";
import { useEffect } from "react";
import { Formik, Field } from "formik";
import type { FieldProps } from "formik";
import { FormItem } from "@/components/ui/Form";
import * as Yup from "yup";
import Button from "@/components/ui/Button";
import Dialog from "@/components/ui/Dialog";
import Select from "@/components/ui/Select";
import Input from "@/components/ui/Input";
import { useTranslation } from "react-i18next";
import Upload from "@/components/ui/Upload";
import {
  apiUploadFile,
  apiPostClintsAttributeByExcel,
} from "@/services/ClientsService";
import ToastNotification from "@/components/shared/myComponents/ToastNotification";
import { HiOutlineCloudUpload, HiOutlineAdjustments } from "react-icons/hi";
import Loading from "@/components/shared/Loading";
import JobProgress from "@/components/shared/JobProgress";
import useExcelImportJob from "@/utils/hooks/useExcelImportJob";

function UpdateAttributeByExcelButton({
  notificationTitle,
  notificationSuccessMessage,
  notificationFailedMessage,
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [isLoading, setIsLoading] = useState(false);

  const [columnsByExcel, setColumnsByExcel] = useState([]);
  const [dataByExcel, setDataByExcel] = useState([]);
  const [columnsOptions, setColumnsOptions] = useState([]);

  const handleOpen = () => setOpen(true);

  const handleClose = () => {
    setOpen(false);
    setColumnsByExcel([]);
    setDataByExcel([]);
  };

  const identifierTypes = [
    { value: "accountNumber", label: t("clients.accountNumber") },
    { value: "identityNumber", label: t("identity") },
  ];

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
            "success",
          );
        } else {
          ToastNotification(
            t(notificationFailedMessage),
            t(notificationTitle),
            "danger",
          );
        }
        setIsLoading(false);
      })
      .catch(() => {
        ToastNotification(
          t(notificationFailedMessage),
          t(notificationTitle),
          "danger",
        );
        setIsLoading(false);
      });
  };

  useEffect(() => {
    setColumnsOptions(
      columnsByExcel.map((member) => ({
        value: member.header,
        label: member.header,
      })),
    );
  }, [columnsByExcel]);

  const Schema = Yup.object().shape({
    identifier_type: Yup.string().required(t("requiredField")),
    identifier_column: Yup.string().required(t("requiredField")),
    value_column: Yup.string().required(t("requiredField")),
    attribute_key: Yup.string().required(t("requiredField")),
  });

  const initialValues = {
    identifier_type: "accountNumber",
    identifier_column: "",
    value_column: "",
    attribute_key: "",
  };

  /* The one place a submit body becomes UI. A background job's `result` is
   * byte-identical to the synchronous response, so both transports land here;
   * `fromJob` only prefixes the toast with "اكتمل". */
  const handleResult = (body, fromJob = false) => {
    const prefix = fromJob ? `${t("job.completed")}: ` : "";
    if (body?.status) {
      ToastNotification(
        prefix + body?.message,
        t("clients.updateAttributeByExcel"),
        "success",
      );
      handleClose();
    } else {
      // نبقي النافذة مفتوحة ليقرأ المستخدم الحسابات التي لم تُطابق
      ToastNotification(
        prefix + body?.message,
        t("clients.updateAttributeByExcel"),
        "danger",
        0,
        true,
      );
    }
    setIsLoading(false);
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
    if (dataByExcel.length === 0) {
      ToastNotification(
        t(notificationFailedMessage),
        t(notificationTitle),
        "danger",
      );
      return;
    }

    setIsLoading(true);
    resetImportJob();

    await apiPostClintsAttributeByExcel(
      {
        identifier_type: values.identifier_type,
        identifier_column: values.identifier_column,
        value_column: values.value_column,
        attribute_key: values.attribute_key.trim(),
        fileDataRow: dataByExcel,
      },
      { async: true },
    )
      .then((response) => {
        /* 202 -> the rows are queued: keep the dialog open on JobProgress.
         * Anything else is the final body and goes straight to handleResult. */
        if (handleImportResponse(response)) {
          setIsLoading(false);
        }
      })
      .catch(() => {
        ToastNotification(
          t(notificationFailedMessage),
          t("clients.updateAttributeByExcel"),
          "danger",
        );
        setIsLoading(false);
      });
  };

  return (
    <div>
      <Button variant="default" size="sm" onClick={handleOpen}
          icon={<HiOutlineAdjustments />}
        >
        {t("clients.updateAttributeByExcel")}
      </Button>

      <Dialog isOpen={open} closable={false}>
        <div>
          <h5 className="py-5">{t("clients.updateAttributeByExcel")}</h5>
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


          <Loading loading={isLoading} type="cover">
            <Formik
              onSubmit={handelSubmitForm}
              initialValues={initialValues}
              validationSchema={Schema}
            >
              {({ values, errors, touched, handleSubmit }) => (
                <form onSubmit={handleSubmit}>
                  <div>
                    <div>
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

                    <div className="grid grid-cols-2 gap-2">
                      <div className="mt-4">
                        <FormItem
                          label={t("clients.identifierType")}
                          invalid={
                            errors.identifier_type && touched.identifier_type
                          }
                          errorMessage={errors.identifier_type}
                        >
                          <Field name="identifier_type">
                            {({ field, form }: FieldProps) => (
                              <Select
                                field={field}
                                form={form}
                                options={identifierTypes}
                                placeholder={t("select")}
                                noOptionsMessage={() => t("noOptionsMessage")}
                                value={
                                  identifierTypes.find(
                                    (option) => option.value === field.value,
                                  ) || ""
                                }
                                onChange={(option) => {
                                  form.setFieldValue(field.name, option.value);
                                }}
                              />
                            )}
                          </Field>
                        </FormItem>
                      </div>

                      <div className="mt-4">
                        <FormItem
                          label={t("clients.identifierColumn")}
                          invalid={
                            errors.identifier_column &&
                            touched.identifier_column
                          }
                          errorMessage={errors.identifier_column}
                        >
                          <Field name="identifier_column">
                            {({ field, form }: FieldProps) => (
                              <Select
                                field={field}
                                form={form}
                                options={columnsOptions}
                                placeholder={t("select")}
                                noOptionsMessage={() => t("noOptionsMessage")}
                                value={
                                  columnsOptions.find(
                                    (option) => option.value === field.value,
                                  ) || ""
                                }
                                onChange={(option) => {
                                  form.setFieldValue(field.name, option.value);
                                }}
                              />
                            )}
                          </Field>
                        </FormItem>
                      </div>

                      <div>
                        <FormItem
                          label={t("clients.attributeValueColumn")}
                          invalid={errors.value_column && touched.value_column}
                          errorMessage={errors.value_column}
                        >
                          <Field name="value_column">
                            {({ field, form }: FieldProps) => (
                              <Select
                                field={field}
                                form={form}
                                options={columnsOptions}
                                placeholder={t("select")}
                                noOptionsMessage={() => t("noOptionsMessage")}
                                value={
                                  columnsOptions.find(
                                    (option) => option.value === field.value,
                                  ) || ""
                                }
                                onChange={(option) => {
                                  form.setFieldValue(field.name, option.value);
                                  // اسم الخاصية يتبع عنوان العمود افتراضياً، ويظل قابلاً للتعديل
                                  if (!values.attribute_key) {
                                    form.setFieldValue(
                                      "attribute_key",
                                      option.value,
                                    );
                                  }
                                }}
                              />
                            )}
                          </Field>
                        </FormItem>
                      </div>

                      <div>
                        <FormItem
                          label={t("clients.attributeKey")}
                          invalid={
                            errors.attribute_key && touched.attribute_key
                          }
                          errorMessage={errors.attribute_key}
                        >
                          <Field name="attribute_key">
                            {({ field, form }: FieldProps) => (
                              <Input
                                {...field}
                                size="sm"
                                placeholder={t("clients.attributeKey")}
                                onChange={(e) => {
                                  form.setFieldValue(
                                    field.name,
                                    e.target.value,
                                  );
                                }}
                              />
                            )}
                          </Field>
                        </FormItem>
                      </div>
                    </div>

                    <div className="my-5">
                      <div className="mt-4 flex flex-wrap items-center justify-end gap-2">
                        <Button
                          variant="solid"
                          type="submit"
                          size="sm"
                          disabled={jobRunning}
                        >
                          {t("save")}
                        </Button>
                        <Button
                          size="sm"
                          variant="default"
                          type="button"
                          onClick={handleClose}
                        >
                          {t("cancel")}
                        </Button>
                      </div>
                    </div>
                  </div>
                </form>
              )}
            </Formik>
          </Loading>
        </div>
      </Dialog>
    </div>
  );
}

export default UpdateAttributeByExcelButton;
