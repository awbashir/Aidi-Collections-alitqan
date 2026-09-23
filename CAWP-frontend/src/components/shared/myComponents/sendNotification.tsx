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
import { apiUploadFile } from "@/services/ArchiveService";
import { apiPostSendNotification } from "@/services/ClientsService";
import ToastNotification from "@/components/shared/myComponents/ToastNotification";
import { HiOutlineCloudUpload, HiOutlineBell } from "react-icons/hi";
import Input from "@/components/ui/Input";
import { m } from "framer-motion";


function SendNotification({
  filter,
  notificationTitle,
  notificationSuccessMessage,
  notificationFailedMessage,
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const handleClose = () => setOpen(false);
  const [is_loading, setIsLoading] = useState(false);
  const [is_loading_file, setIsLoadingFile] = useState(false);

  const [columnsByExcel, setColumnsByExcel] = useState([]);
  const [excel_data, setExcel_data] = useState([]);

  const handleOpen = (e, id) => {
    setOpen(true);
  };

  const handelInquiryClint = (values) => {
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
      message: values.notification,
    };
    setIsLoading(true);
    apiPostSendNotification(result)
      .then((response) => {
        if (response.data.status) {
          setIsLoading(false);
          ToastNotification(
            t(notificationSuccessMessage),
            t(notificationTitle),
            "success"
          );
          handleClose();
        } else {
          console.log(error);
          ToastNotification(
            t(notificationFailedMessage),
            t(notificationTitle),
            "danger"
          );
          setIsLoading(false);
         
        }
      })
      .catch((error) => {
        console.log(error);
        ToastNotification(
          t(notificationFailedMessage),
          t(notificationTitle),
          "danger"
        );
        setIsLoading(false);
      });
   
  };

  const handleChangeByExcel = async (event) => {
    setIsLoadingFile(true);
    const formData = new FormData();
    formData.append("file", event[0]);
    formData.append("fileName", event[0].name);
    await apiUploadFile(formData)
      .then((response) => {
        if (response.data.status) {
          setIsLoadingFile(false);
          setColumnsByExcel(response.data.data);
          setExcel_data(response.data.excel_data);
          ToastNotification(
            t(notificationSuccessMessage),
            t(notificationTitle),
            "success"
          );
        } else {
          console.log(error);
          ToastNotification(
            t(notificationFailedMessage),
            t(notificationTitle),
            "danger"
          );
          setIsLoadingFile(false);
        }
      })
      .catch((error) => {
        console.log(error);
        ToastNotification(
          t(notificationFailedMessage),
          t(notificationTitle),
          "danger"
        );
        SetIsLoadingFile(false);
      });
  };

  const initialValues = {
    account_number: "",
    notification: "",
  };

  const SchemaArchiveUser = Yup.object().shape({
    account_number: Yup.string().required("هذا الحقل مطلوب"),
    notification: Yup.string().required("هذا الحقل مطلوب"),
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
          icon={<HiOutlineBell />}
        >
          {t("sendNotification")}{" "}
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
            <h5 className="py-5">{t("sendNotificationToCollector")}</h5>

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
                            loading={is_loading_file}
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
                        <FormItem
                          className="col-span"
                          label={t("notificationMessage")}
                          invalid={
                            errors.notification && touched.notification
                          }
                          errorMessage={errors.notification}
                        >
                          <Field 
                          name="notification"
                          textArea
                          placeholder={t("notificationMessage")}
                           component={Input}

                          />
                           
                        
                        </FormItem>
                      </div>

                      <div class="my-5" item xs={12} md={12}>
                        <div className="mt-4 flex flex-wrap items-center justify-end gap-2">
                          <>
                            <Button
                              variant="solid"
                              size="sm"
                              type="submit"
                            >
                              {t("send")}
                            </Button>
                            <Button
                              variant="default"
                              size="sm"
                              onClick={handleClose}
                            >
                              {t("cancel")}
                            </Button>
                          </>
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

export default SendNotification;
