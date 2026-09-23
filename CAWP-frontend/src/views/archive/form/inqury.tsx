import React from "react";
import { useState } from "react";
import { Formik, Field, FieldProps } from "formik";
import { FormItem } from "@/components/ui/Form";
import Button from "@/components/ui/Button";
import Select from "@/components/ui/Select";
import { useTranslation } from "react-i18next";

import * as Yup from "yup";
import { handelInquiryClint } from "@/services/ArchiveService";
const InquiryForm = (columns) => {
  const { t } = useTranslation();
  const SchemaArchiveUser = Yup.object().shape({
    account_number: Yup.string().required("هذا الحقل مطلوب"),
  });

  const initialValues = {
    account_number: "",
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
    // handleClose();
  };

  const [columnsOptions, setColumnsOptions] = useState([]);

  return (
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
          <div>
            <div>
              <FormItem
                className="col-span"
                label={t("clients.accountNumber")}
                invalid={errors.account_number && touched.account_number}
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
                          form.setFieldValue(field.name, option.value);
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
                  >
                    {t("inquiry")}
                  </Button>
                  <Button
                    variant="default" /* onClick={handleClose} */
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
  );
};

export default InquiryForm;
