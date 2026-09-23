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
  apiPostClintSepcifyUsersByExcel,
} from "@/services/ClientsService";
import ToastNotification from "@/components/shared/myComponents/ToastNotification";
import { HiOutlineCloudUpload, HiPlusCircle, HiOutlineSearch } from "react-icons/hi";
import Input from "@/components/ui/Input";
import { useAppSelector, useAppDispatch, getContracts } from "../store";
import Loading from "@/components/shared/Loading";

function EnquiryByExcelButton({
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
    account_number: Yup.string().required(t("requiredField")),
  });

  const initialValues = {
    account_number: "",
  };

  const handelSubmitForm = async (values) => {
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

  return (
    <div>
      <div>
        <Button variant="default" size="sm" onClick={handleOpen}
          icon={<HiOutlineSearch />}
        >
          {t("clients.EnquiryByExcel")}
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
            <h5 className="pb-4 text-[16px] font-extrabold text-navy-600 dark:text-night-body">{t("clients.EnquiryByExcel")}</h5>

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
                          <FormItem
                            className="col-span"
                            label={t("clients.fieldAccountNumber")}
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
                                    noOptionsMessage={() =>
                                      t("noOptionsMessage")
                                    }
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

                        <div class="my-5" item xs={12} md={12}>
                          <div className="mt-4 flex flex-wrap items-center justify-end gap-2">
                            <div className="flex flex-wrap items-center gap-2">
                              <Button
                                variant="solid"
                                type="submit"
                                size="sm"
                              >
                                {t("inquiry")}
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

export default EnquiryByExcelButton;
