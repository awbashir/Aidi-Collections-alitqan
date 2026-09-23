import { useEffect, useState, forwardRef } from "react";
import { FormItem, FormContainer } from "@/components/ui/Form";
import Input from "@/components/ui/Input";
import Button from "@/components/ui/Button";
import Checkbox from "@/components/ui/Checkbox";
import { Field, Form, Formik, FormikProps } from "formik";
import { HiOutlineEyeOff, HiOutlineEye, HiOutlinePencil } from "react-icons/hi";
import * as Yup from "yup";
import type { MouseEvent } from "react";
import { useTranslation } from "react-i18next";
import Select from "@/components/ui/Select";
import {
  getCollectionMethods,
  getCollectionTypes,
  getUsers,
  getPaymentDetails,
  useAppDispatch,
  useAppSelector,
} from "../../store";

export type FormikRef = FormikProps;

const EditPaymentForm = forwardRef<forwardRef>((props, ref) => {
  const { onFormSubmit, id } = props;
  const { t } = useTranslation();
  const dispatch = useAppDispatch();
  const { collectionMethods, collectionTypes, users, paymentDetails } =
    useAppSelector((state) => state.currentUser_paymentsList.data);
  const optionsCollectionMethods = collectionMethods?.map((reason) => ({
    label: reason.name,
    value: reason.id,
  }));
  const optionsCollectionTypes = collectionTypes?.map((reason) => ({
    label: reason.name,
    value: reason.id,
  }));
  const optionsUsers = users?.map((reason) => ({
    label: reason.name,
    value: reason.id,
  }));
  useEffect(() => {
    dispatch(getPaymentDetails(id));
    dispatch(getCollectionMethods());
    dispatch(getCollectionTypes());
    dispatch(getUsers());
  }, [dispatch]);

  const validationSchema = Yup.object().shape({
    amount: Yup.number().required(t("payments.amountRequired")),
    payment_date: Yup.string().required(t("payments.dateRequired")),
    collection_method_id: Yup.string().required(t("payments.methodRequired")),
    collection_type_id: Yup.string().required(t("payments.typeRequired")),
    user_id: Yup.string().required(t("payments.userRequired")),

    ArchiveReason: Yup.string().required(t("requiredField")),
  });

  const initialValues = {
    amount: paymentDetails?.amount,
    payment_date: paymentDetails?.payment_date,
    collection_method_id: paymentDetails?.collection_method_id,
    collection_type_id: paymentDetails?.collection_type_id,
    user_id: paymentDetails?.user_id,
  };

  return (
    <div className="w-[400px]">
      <Formik
        innerRef={ref}
        initialValues={initialValues}
        validationSchema={validationSchema}
        onSubmit={(values, { setSubmitting }) => {
          onFormSubmit?.(values);
          setSubmitting(false);
        }}
        enableReinitialize={true}
      >
        {({ touched, errors, values, resetForm }) => (
          <Form>
            <FormContainer>
              <FormItem
                asterisk
                label={t("amount")}
                invalid={errors.amount && touched.amount}
                errorMessage={errors.amount}
              >
                <Field
                  name="amount"
                  type="number"
                  component={Input}
                  placeholder={t("amount")}
                ></Field>
              </FormItem>

              <FormItem
                asterisk
                label={t("payments.date")}
                invalid={errors.payment_date && touched.payment_date}
                errorMessage={errors.payment_date}
              >
                <Field
                  name="payment_date"
                  type="date"
                  component={Input}
                  placeholder={t("payments.date")}
                ></Field>
              </FormItem>

              <FormItem
                asterisk
                label={t("payments.method")}
                invalid={
                  errors.collection_method_id && touched.collection_method_id
                }
                errorMessage={errors.collection_method_id}
              >
                <Field name="collection_method_id">
                  {({ field, form }: FieldProps<FormModel>) => (
                    <Select
                      placeholder={t("payments.selectMethod")}
                      field={field}
                      form={form}
                      options={optionsCollectionMethods}
                      value={optionsCollectionMethods?.filter(
                        (option) => option.value === values.collection_method_id
                      )}
                      onChange={(option) =>
                        form.setFieldValue(field.name, option?.value)
                      }
                    />
                  )}
                </Field>
              </FormItem>

              <FormItem
                asterisk
                label={t("payments.type")}
                invalid={
                  errors.collection_type_id && touched.collection_type_id
                }
                errorMessage={errors.collection_type_id}
              >
                <Field name="collection_type_id">
                  {({ field, form }: FieldProps<FormModel>) => (
                    <Select
                      placeholder={t("payments.type")}
                      field={field}
                      form={form}
                      options={optionsCollectionTypes}
                      value={optionsCollectionTypes?.filter(
                        (option) => option.value === values.collection_type_id
                      )}
                      onChange={(option) =>
                        form.setFieldValue(field.name, option?.value)
                      }
                    />
                  )}
                </Field>
              </FormItem>

              <FormItem
                asterisk
                label={t("users.name")}
                invalid={errors.user_id && touched.user_id}
                errorMessage={errors.user_id}
              >
                <Field name="user_id">
                  {({ field, form }: FieldProps<FormModel>) => (
                    <Select
                      placeholder={t("users.name")}
                      field={field}
                      form={form}
                      options={optionsUsers}
                      value={optionsUsers?.filter(
                        (option) => option.value === values.user_id
                      )}
                      onChange={(option) =>
                        form.setFieldValue(field.name, option?.value)
                      }
                    />
                  )}
                </Field>
              </FormItem>
              {/*    <FormItem
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
              </FormItem> */}
            </FormContainer>
          </Form>
        )}
      </Formik>
    </div>
  );
});

EditPaymentForm.displayName = "EditPaymentForm";

export default EditPaymentForm;
