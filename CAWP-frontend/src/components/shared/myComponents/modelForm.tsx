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
import ToastNotification from "@/components/shared/myComponents/ToastNotification";
import { HiOutlineCloudUpload, HiOutlineSearch } from "react-icons/hi";

function InquiryClintByExcel({
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

  const handleOpen = (e, id) => {
    setOpen(true);
  };

  const handelInquiryClint = (values) => {
    var data = dataByExcel;

    // print the data in the console key is account_number
    var result = [];
    data.map((item) => {
      if (item[values.account_number] !== undefined) {
        result.push(item[values.account_number]);
      }
    });
    setInquiryClintByExcel(result);
    filter(result);
    handleClose();
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
    let w = columnsByExcel.map((member) => ({
      value: member.header,
      label: member.header,
    }));
    setColumnsOptions(w);
  }, [columnsByExcel]);

  return (
    <div>
      <div>
        <Button variant="default" size="sm" onClick={handleOpen}
          icon={<HiOutlineSearch />}
        >
          {t("queryByExcel")}{" "}
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
            <h5 className="py-5">{t("queryByExcel")}</h5>

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

                      {/* components.md: actions on the inline-end edge,
                          secondary then primary — under RTL the primary is the
                          left-most button. */}
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
                          <Button variant="solid" size="sm" type="submit">
                            {t("inquiry")}
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

export default InquiryClintByExcel;
