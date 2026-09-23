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

const RejectPaymentForm = forwardRef<forwardRef>((props, ref) => {
  const { onFormSubmit } = props;
  const { t } = useTranslation();
  const dispatch = useAppDispatch();
  const { archiveReasons } = useAppSelector(
    (state) => state.currentUser_paymentsList.data
  );
  const options = archiveReasons?.map((reason) => ({
    label: reason.name,
    value: reason.id,
  }));
  useEffect(() => {
    dispatch(getArchiveReasons());
  }, [dispatch]);

  const validationSchema = Yup.object().shape({
    approval_comment: Yup.string().required(t("requiredField")),
  });

  return (
    <div className="w-[400px]">
      <Formik
        innerRef={ref}
        initialValues={{
          approval_comment: "",
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
                label={t("rejectReason")}
                invalid={errors.approval_comment && touched.approval_comment}
                errorMessage={errors.approval_comment}
              >
                <Field
                  name="approval_comment"
                  placeholder={t("rejectReason")}
                  as={Input}
                />
              </FormItem>
            </FormContainer>
          </Form>
        )}
      </Formik>
    </div>
  );
});

RejectPaymentForm.displayName = "RejectPaymentForm";

export default RejectPaymentForm;
