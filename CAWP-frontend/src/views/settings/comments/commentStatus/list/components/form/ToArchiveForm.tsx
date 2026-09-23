import { useEffect, useState, forwardRef } from "react";
import { FormItem, FormContainer } from "@/components/ui/Form";
import Input from "@/components/ui/Input";
import Button from "@/components/ui/Button";
import Checkbox from "@/components/ui/Checkbox";
import { Field, Form, Formik, FormikProps } from "formik";
import { HiOutlineEyeOff, HiOutlineEye } from "react-icons/hi";
import * as Yup from "yup";
import type { MouseEvent } from "react";
import { useTranslation } from "react-i18next";
import Select from "@/components/ui/Select";
import { getArchiveReasons, useAppDispatch, useAppSelector } from "../../store";

export type FormikRef = FormikProps;

const ToArchiveForm = forwardRef<forwardRef>((props, ref) => {
  const { onFormSubmit } = props;
  const { t } = useTranslation();
  const dispatch = useAppDispatch();
  const { archiveReasons } = useAppSelector(
    (state) => state.commentStatusList.data
  );
  const options = archiveReasons?.map((reason) => ({
    label: reason.name,
    value: reason.id,
  }));
  useEffect(() => {
    dispatch(getArchiveReasons());
  }, [dispatch]);

  const validationSchema = Yup.object().shape({
    ArchiveReason: Yup.string().required(t("requiredField")),
  });

  return (
    <div className="w-[400px]">
      <Formik
        innerRef={ref}
        initialValues={{
          ArchiveReason: "",
        }}
        validationSchema={validationSchema}
        onSubmit={(values, { setSubmitting }) => {
          onFormSubmit?.(values);
          setSubmitting(false);
        }}
      >
        {({ touched, errors, values, resetForm }) => (
          <Form>
            <FormContainer>
              <FormItem
                asterisk
                label={t("ArchiveReason")}
                invalid={errors.ArchiveReason && touched.ArchiveReason}
                errorMessage={errors.ArchiveReason}
              >
                <Field name="ArchiveReason">
                  {({ field, form }: FieldProps<FormModel>) => (
                    <Select
                      placeholder={t("select")}
                      field={field}
                      form={form}
                      options={options}
                      value={options?.filter(
                        (option) => option.value === values.ArchiveReason
                      )}
                      onChange={(option) =>
                        form.setFieldValue(field.name, option?.value)
                      }
                    />
                  )}
                </Field>
              </FormItem>
            </FormContainer>
          </Form>
        )}
      </Formik>
    </div>
  );
});

ToArchiveForm.displayName = "ToArchiveForm";

export default ToArchiveForm;
