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
import { mainStatusesFor } from "@/utils/commentStatusTree";
import { useTranslation } from "react-i18next";
import Upload from "@/components/ui/Upload";
import {
  apiUploadFile2,
  apiPostcommentsByExcel,
  apiGetContactsMethods,
  apiGetCommentReachability,
  apiGetCommentStatus,
  apiGetContracts,
} from "@/services/ClientsService";
import ToastNotification from "@/components/shared/myComponents/ToastNotification";
import { HiOutlineCloudUpload, HiPlusCircle, HiOutlineChatAlt2 } from "react-icons/hi";
import Input from "@/components/ui/Input";
import { useAppSelector, useAppDispatch } from "../store";
import Loading from "@/components/shared/Loading";
import JobProgress from "@/components/shared/JobProgress";
import useExcelImportJob from "@/utils/hooks/useExcelImportJob";

function CommentByExcelButton({
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
  const [excel_data, setExcel_data] = useState([]);

  const [columnsByExcel, setColumnsByExcel] = useState([]);
  const [dataByExcel, setDataByExcel] = useState([]);
  const [inquiryClintByExcel, setInquiryClintByExcel] = useState([]);

  const [contactMethods, setClintContacts] = useState([]);
  const [lastComment, setLastComment] = useState([]);
  const [commentReachability, setCommentReachability] = useState([]);
  const [commentStatus, setCommentStatus] = useState([]);
  const [contracts, setContracts] = useState([]);

  const handleOpen = (e, id) => {
    setOpen(true);
  };

  const handleChangeByExcel = async (event) => {
    const formData = new FormData();
    formData.append("file", event[0]);
    formData.append("fileName", event[0].name);

    await apiUploadFile2(formData)
      .then((response) => {
        if (response.data.status) {
          setColumnsByExcel(response.data.data);
          setDataByExcel(response.data.excel_data);
          setExcel_data(response.data.excel_data);
          ToastNotification(
            t(notificationSuccessMessage),
            t(notificationTitle),
            "success"
          );
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
    let w = columnsByExcel.map((member, idx) => ({
      value: member.header,
      label: member.header,
      index: idx,
    }));
    setColumnsOptions(w);
  }, [columnsByExcel]);

  const dispatch = useAppDispatch();

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

  const Schema = Yup.object().shape({
    contract_id: Yup.string().required(t("requiredField")),
    contactMethod_id: Yup.string().required(t("requiredField")),
    comment_reachability_id: Yup.string().required(t("requiredField")),
    comment_status_id: Yup.string().required(t("requiredField")),
    comment: Yup.string().required(t("requiredField")),
    data1: Yup.string().required(t("requiredField")),
  });

  const initialValues = {
    call_id: "",
    clint_id: "",
    contactNumber: "",
    contract_id: "",
    contactMethod_id: "",
    comment_reachability_id: "",
    comment_status_id: "",
    comment: "",
    data1: "",
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
    //get index of columnsOptions
    var index = "";
    columnsOptions.map((item) => {
      if (item.value === values.data1) {
        index = item.index;
      }
    });

    //add index to values as data1_index
    values.index = index;
    values.excel_data = excel_data;
    setIsLoading(true);
    resetImportJob();

    await apiPostcommentsByExcel(values, { async: true })
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
  const fetchContactsMethods = async () => {
    const response = await apiGetContactsMethods();
    setClintContacts(response.data.contact_methods);
  };

  const fetchCommentReachability = async () => {
    const response = await apiGetCommentReachability();
    setCommentReachability(response.data.comment_reachabilitys);
  };

  const fetchCommentStatus = async () => {
    const response = await apiGetCommentStatus();
    setCommentStatus(response.data.comment_statuses);
  };

  const fetchContracts = async () => {
    const response = await apiGetContracts();
    setContracts(response.data.contracts);
  };

  const contactsMethodsOtions = contactMethods?.map((option) => ({
    label: option.name,
    value: option.id,
  }));

  const commentsReachabilityOptions = commentReachability?.map((option) => ({
    label: option.name,
    value: option.id,
  }));

  const commentsStatusOptions = commentStatus?.map((option) => ({
    label: option.name,
    value: option.id,
    reachability_id: option.CommentReachability_id,
    contract_id: option.contract_id,
    parent_id: option.parent_id ?? null,
    deleted_at: option.deleted_at ?? null,
  }));

  const contractsOptions = contracts?.map((option) => ({
    label: option.name,
    value: option.id,
  }));

  useEffect(() => {
    if (open) {
      fetchContactsMethods();
      fetchCommentReachability();
      fetchCommentStatus();
      fetchContracts();
    }
  }, [open]);

  return (
    <div>
      <div>
        <Button variant="default" size="sm" onClick={handleOpen}
          icon={<HiOutlineChatAlt2 />}
        >
          {t("addcommentsByExcel")}
        </Button>

        <Dialog isOpen={open} closable={false}>
          <div>
            <h5 className="py-5">{t("clients.updateByExcel")}</h5>
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
                              label={t("contracts.title")}
                              invalid={
                                errors.contract_id && touched.contract_id
                              }
                              errorMessage={errors.contract_id}
                            >
                              <Field name="contract_id">
                                {({ field, form }: FieldProps) => (
                                  <Select
                                    field={field}
                                    form={form}
                                    options={contractsOptions}
                                    value={contractsOptions?.filter(
                                      (category) =>
                                        category.value === values.contract_id
                                    )}
                                    onChange={(option) => {
                                      form.setFieldValue(
                                        field.name,
                                        option?.value
                                      );
                                    }}
                                    placeholder={t("select")}
                                  />
                                )}
                              </Field>
                            </FormItem>
                          </div>
                          <div className="mt-4">
                            <FormItem
                              label={t("comments.contactMethod")}
                              invalid={
                                errors.contactMethod_id &&
                                touched.contactMethod_id
                              }
                              errorMessage={errors.contactMethod_id}
                            >
                              <Field name="contactMethod_id">
                                {({ field, form }: FieldProps) => (
                                  <Select
                                    field={field}
                                    form={form}
                                    options={contactsMethodsOtions}
                                    value={contactsMethodsOtions?.filter(
                                      (category) =>
                                        category.value ===
                                        values.contactMethod_id
                                    )}
                                    onChange={(option) => {
                                      form.setFieldValue(
                                        field.name,
                                        option?.value
                                      );
                                    }}
                                    placeholder={t("select")}
                                  />
                                )}
                              </Field>
                            </FormItem>
                          </div>

                          <div className="">
                            <FormItem
                              label={t("comments.reachability")}
                              invalid={
                                errors.comment_reachability_id &&
                                touched.comment_reachability_id
                              }
                              errorMessage={errors.comment_reachability_id}
                            >
                              <Field name="comment_reachability_id">
                                {({ field, form }: FieldProps) => (
                                  <Select
                                    field={field}
                                    form={form}
                                    options={commentsReachabilityOptions}
                                    value={commentsReachabilityOptions?.filter(
                                      (category) =>
                                        category.value ===
                                        values.comment_reachability_id
                                    )}
                                    onChange={(option) =>
                                      form.setFieldValue(
                                        field.name,
                                        option?.value
                                      )
                                    }
                                    placeholder={t("select")}
                                  />
                                )}
                              </Field>
                            </FormItem>
                          </div>

                          <div className="">
                            <FormItem
                              label={t("comments.status")}
                              invalid={
                                errors.comment_status_id &&
                                touched.comment_status_id
                              }
                              errorMessage={errors.comment_status_id}
                            >
                              <Field name="comment_status_id">
                                {({ field, form }: FieldProps) => (
                                  <Select
                                    field={field}
                                    form={form}
                                    options={mainStatusesFor(
                                      commentsStatusOptions,
                                      values?.contract_id
                                    ).filter(
                                      (commentStatus) =>
                                        values.comment_reachability_id ==
                                        commentStatus.reachability_id
                                    )}
                                    value={commentsStatusOptions?.filter(
                                      (category) =>
                                        category.value ===
                                        values.comment_status_id
                                    )}
                                    onChange={(option) =>
                                      form.setFieldValue(
                                        field.name,
                                        option?.value
                                      )
                                    }
                                    placeholder={t("select")}
                                  />
                                )}
                              </Field>
                            </FormItem>
                          </div>

                          <div className="col-span-2">
                            <FormItem
                              label={t("comments.comment")}
                              invalid={errors.comment && touched.comment}
                              errorMessage={errors.comment}
                            >
                              <Field
                                size="sm"
                                type="text"
                                autoComplete="off"
                                name="comment"
                                placeholder={t("comments.comment")}
                                component={Input}
                                textArea
                              />
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
                        </div>
                        <div className="my-5">
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

export default CommentByExcelButton;
