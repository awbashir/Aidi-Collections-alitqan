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
import { apiUploadFile, apiUnarchiveByExcel } from "@/services/ArchiveService";
import ToastNotification from "@/components/shared/myComponents/ToastNotification";
import JobProgress from "@/components/shared/JobProgress";
import useExcelImportJob from "@/utils/hooks/useExcelImportJob";
import { HiOutlineCloudUpload, HiOutlineArchive } from "react-icons/hi";

function MoveFromArchiveByExcel({
  filter,
  notificationTitle,
  notificationSuccessMessage,
  notificationFailedMessage,
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const handleClose = () => setOpen(false);

  const [columnsByExcel, setColumnsByExcel] = useState([]);
  const [dataByExcel, setDataByExcel] = useState([]);
  const [inquiryClintByExcel, setInquiryClintByExcel] = useState([]);

  const [excel_data, setExcel_data] = useState([]);

  const handleOpen = (e, id) => {
    setOpen(true);
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
            : t("archive.successMoveFromArchive")),
        t("archive.moveFromArchive"),
        "success"
      );
    } else {
      ToastNotification(
        prefix +
          (fromJob && body?.message
            ? body.message
            : t("archive.failMoveFromArchive")),
        t("archive.moveFromArchive"),
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

  const handelInquiryClint = async (values) => {
    //get index of columnsOptions
    var index = "";
    columnsOptions.map((item) => {
      if (item.value === values.account_number) {
        index = item.index;
      }
    });

    var result = {
      file_name: excel_data,
      index: index,
    };

    resetImportJob();

    await apiUnarchiveByExcel(result, { async: true })
      .then((response) => {
        /* 202 -> the rows are queued: keep the dialog open on JobProgress.
         * Anything else is the final body and goes straight to handleResult. */
        handleImportResponse(response);
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
  const handleChangeByExcel = async (event) => {
    const formData = new FormData();
    formData.append("file", event[0]);
    formData.append("fileName", event[0].name);

    await apiUploadFile(formData)
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

  const initialValues = {
    account_number: "",
  };

  const SchemaArchiveUser = Yup.object().shape({
    account_number: Yup.string().required("هذا الحقل مطلوب"),
  });

  const [columnsOptions, setColumnsOptions] = useState([]);

  useEffect(() => {
    let w = columnsByExcel.map((member, idx) => ({
      value: member.header,
      label: member.header,
      index: idx,
    }));
    setColumnsOptions(w);
  }, [excel_data]);

  return (
    <div>
      <div>
        <Button variant="default" size="sm" onClick={handleOpen}
          icon={<HiOutlineArchive />}
        >
          {t("archive.moveFromArchive")}{" "}
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
            <h5 className="py-5">{t("archive.moveFromArchive")}</h5>
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
                          >
                            {t("uploadExcel")}
                          </Button>
                        </Upload>
                      </div>

                      <div class="grid" item xs={12} md={12}>
                        <FormItem
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
                        </FormItem>
                      </div>

                      <div className="my-5">
                        <div className="mt-4 flex flex-wrap items-center justify-end gap-2">
                          <Button
                            variant="default"
                            size="sm"
                            type="button"
                            onClick={handleClose}
                          >
                            {t("cancel")}
                          </Button>
                          <Button
                            variant="solid"
                            size="sm"
                            type="submit"
                            disabled={jobRunning}
                          >
                            {t("move")}
                          </Button>
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

export default MoveFromArchiveByExcel;
