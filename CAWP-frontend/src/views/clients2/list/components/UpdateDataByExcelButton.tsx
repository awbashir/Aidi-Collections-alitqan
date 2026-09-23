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
  apiPostClintsUpdateByExcel,
} from "@/services/ClientsService";
import ToastNotification from "@/components/shared/myComponents/ToastNotification";
import { HiOutlineCloudUpload, HiPlusCircle, HiOutlineRefresh } from "react-icons/hi";
import Input from "@/components/ui/Input";
import { useAppSelector, useAppDispatch, getUpdateSides } from "../store";
import Loading from "@/components/shared/Loading";
import JobProgress from "@/components/shared/JobProgress";
import useExcelImportJob from "@/utils/hooks/useExcelImportJob";

function UpdateDataByExcelButton({
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

  const { updateSides } = useAppSelector((state) => state.clientsList2.data);

  const replaceTypeUpdate = (update_type) => {
    let updateType;

    switch (update_type) {
      case 1:
        updateType = "ارقام التواصل";
        break;
      case 2:
        updateType = "مبلغ المديونية";
        break;
      case 3:
        updateType = "رقم الهوية";
        break;
      case 4:
        updateType = "اسم العميل برقم الحساب";
        break;
      case 5:
        updateType = "اسم العميل برقم الهوية";
        break;
      default:
        updateType = "";
    }
    return updateType;
  };

  const replaceIdentityNumberAndAccountNumber = (update_type) => {
    let updateType;
    let identityNumber = "عمود رقم الهوية";
    let accountNumber = "عمود رقم الحساب";

    switch (update_type) {
      case 1:
        updateType = identityNumber;
        break;
      case 2:
        updateType = accountNumber;
        break;
      case 3:
        updateType = accountNumber;
        break;
      case 4:
        updateType = accountNumber;
        break;
      case 5:
        updateType = identityNumber;
        break;
      default:
        updateType = "";
    }
    return updateType;
  };

  const updateTypes = [
    { value: 1, label: t("contactsNumber") },
    { value: 2, label: t("amountdebt") },
    { value: 3, label: t("identity") },
    { value: 4, label: t("clientNameByAccountNumber") },
    { value: 5, label: t("clientNameByIdNumber") },
  ];

  const optionsUpdateSides = updateSides?.map((updateSide) => ({
    label: updateSide.name,
    value: updateSide.id,
  }));

  useEffect(() => {
    if (open) {
      dispatch(getUpdateSides());
    }
  }, [open]);

  const Schema = Yup.object().shape({
    update_type: Yup.string().required(t("requiredField")),
    update_side: Yup.string().required(t("requiredField")),
    data1: Yup.string().required(t("requiredField")),
    data2: Yup.string().required(t("requiredField")),
  });

  const initialValues = {
    update_type: "",
    update_side: "",
    data1: "",
    data2: "",
  };

  /* The one place a submit body becomes UI. A background job's `result` is
   * byte-identical to the synchronous response, so both transports land here;
   * `fromJob` only prefixes the toast with "اكتمل". */
  const handleResult = (body, fromJob = false) => {
    const prefix = fromJob ? `${t("job.completed")}: ` : "";
    if (body?.status) {
      ToastNotification(
        prefix +
          (fromJob && body?.message ? body.message : t("clients.SepcifyClientToUserByExcelSuccess")),
        t("clients.SepcifyClientToUserByExcel"),
        "success"
      );
      setIsLoading(false);
      handleClose();
    } else {
      ToastNotification(
        prefix + body?.message,
        t("clients.SepcifyClientToUserByExcel"),
        "danger",
        0,
        true
      );
      setIsLoading(false);
    }
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
    sendvalue.file = columnsByExcel;
    sendvalue = {
      update_type: values.update_type.toString(),
      update_side: values.update_side,
      data1: values.data1,
      data2: values.data2,
      fileData: columnsByExcel,
      fileDataRow: dataByExcel,
    };

    setIsLoading(true);
    resetImportJob();

    await apiPostClintsUpdateByExcel(sendvalue, { async: true })
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
        setIsLoading(false);
      });
  };

  return (
    <div>
      <div>
        <Button variant="default" size="sm" onClick={handleOpen}
          icon={<HiOutlineRefresh />}
        >
          {t("updateByExcel")}
        </Button>

        <Dialog isOpen={open} closable={false}>
          <div>
            <h5 className="pb-4 text-[16px] font-extrabold text-navy-600 dark:text-night-body">{t("clients.updateByExcel")}</h5>
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

                        <div className="grid grid-cols-2 gap-2">
                          <div className="mt-4">
                            <FormItem
                              label={t("updateType")}
                              invalid={
                                errors.update_type && touched.update_type
                              }
                              errorMessage={errors.update_type}
                            >
                              <Field name="update_type">
                                {({ field, form }: FieldProps) => {
                                  return (
                                    <Select
                                      field={field}
                                      form={form}
                                      options={updateTypes}
                                      placeholder={t("select")}
                                      noOptionsMessage={() =>
                                        t("noOptionsMessage")
                                      }
                                      value={
                                        updateTypes.find(
                                          (option) =>
                                            option.value === field.value
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
                            </FormItem>
                          </div>

                          <div className="mt-4">
                            <FormItem
                              label={t("updateSide")}
                              invalid={
                                errors.update_side && touched.update_side
                              }
                              errorMessage={errors.update_side}
                            >
                              <Field name="update_side">
                                {({ field, form }: FieldProps) => {
                                  return (
                                    <Select
                                      field={field}
                                      form={form}
                                      options={optionsUpdateSides}
                                      placeholder={t("select")}
                                      noOptionsMessage={() =>
                                        t("noOptionsMessage")
                                      }
                                      value={
                                        optionsUpdateSides.find(
                                          (option) =>
                                            option.value === field.value
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
                            </FormItem>
                          </div>

                          <div>
                            <FormItem
                              label={replaceIdentityNumberAndAccountNumber(
                                values.update_type
                              )}
                              invalid={errors.data1 && touched.data1}
                              errorMessage={errors.data1}
                            >
                              <Field name="data1">
                                {({ field, form }: FieldProps) => {
                                  return (
                                    <Select
                                      field={field}
                                      form={form}
                                      options={columnsOptions}
                                      placeholder={t("select")}
                                      noOptionsMessage={() =>
                                        t("noOptionsMessage")
                                      }
                                      value={
                                        columnsOptions.find(
                                          (option) =>
                                            option.value === field.value
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
                            </FormItem>
                          </div>

                          <div>
                            <FormItem
                              className="col-span"
                              label={replaceTypeUpdate(values.update_type)}
                              invalid={errors.data2 && touched.data2}
                              errorMessage={errors.data2}
                            >
                              <Field name="data2">
                                {({ field, form }: FieldProps) => {
                                  return (
                                    <Select
                                      field={field}
                                      form={form}
                                      options={columnsOptions}
                                      placeholder={t("select")}
                                      noOptionsMessage={() =>
                                        t("noOptionsMessage")
                                      }
                                      value={
                                        columnsOptions.find(
                                          (option) =>
                                            option.value === field.value
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
                            </FormItem>
                          </div>
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

export default UpdateDataByExcelButton;
