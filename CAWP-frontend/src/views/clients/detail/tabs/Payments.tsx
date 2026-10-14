import Segment from "@/components/ui/Segment";
import Button from "@/components/ui/Button";
import { FormContainer } from "@/components/ui/Form";
import Notification from "@/components/ui/Notification";
import toast from "@/components/ui/toast";
import FormDesription from "./FormDesription";
import FormRow from "./FormRow";
import { Field, Form, Formik } from "formik";
import isLastChild from "@/utils/isLastChild";
import type {
  FieldProps,
  FormikTouched,
  FormikErrors,
  FieldInputProps,
  FormikProps,
} from "formik";
import * as Yup from "yup";
import { useTranslation } from "react-i18next";
import Input from "@/components/ui/Input";
import Select from "@/components/ui/Select";
import {
  apiGetCollectionTypes,
  apiGetCollectionMethods,
} from "@/services/ClientsService";
import { useState, useEffect } from "react";
import {
  useAppSelector,
  useAppDispatch,
  postPayments,
  getPayments,
} from "../store";
import { init, use } from "i18next";
import TimeLineComment from "../components/Timeline";
import PaymentsTable from "../tables/payments2/Portfolios";
import PaymentClient from "../components/PaymentClientTable";
import { ReceiptPicker, describeReceiptError } from "@/views/paymentAttachments";
import { paymentAttachmentsApi } from "@/services/v2/payAttApi";
import { newIdempotencyKey } from "@/services/v2";

const Comments = ({
  data = {
    news: [],
    accountActivity: [],
    signIn: [],
    reminders: [],
    mentioned: [],
    replies: [],
    taskUpdate: [],
    assigned: [],
    newProduct: [],
    newOrder: [],
  },
}: NotificationSettingProps) => {
  const { t } = useTranslation();
  const dispatch = useAppDispatch();

  const validationSchema = Yup.object().shape({
    collection_type_id: Yup.string().required(t("requiredField")),
    collection_method_id: Yup.string().required(t("requiredField")),
    payment_date: Yup.string().required(t("requiredField")),
    amount: Yup.string().required(t("requiredField")),
  });
  const clint = useAppSelector(
    (state) => state.crmCustomerDetails?.data?.clientData
  );

  const userId = useAppSelector((state) => state.auth.user.id);

  const initialValues = {
    collection_type_id: "",
    collection_method_id: "",
    payment_date: "",
    amount: "",
    user_id: userId,
    clint_id: clint?.id,
  };

  const [collectionType, setCollectionType] = useState([]);
  // optional receipt, uploaded right after the payment is saved
  const [receipt, setReceipt] = useState<File | null>(null);
  const [collectionMethods, setCollectionMethods] = useState([]);

  const collectionTypeOptions = collectionType?.map((option) => ({
    label: option.name,
    value: option.id,
  }));

  const collectionMethodOptions = collectionMethods?.map((option) => ({
    label: option.name,
    value: option.id,
  }));

  const fetchCollectionType = async () => {
    const response = await apiGetCollectionTypes();
    setCollectionType(response.data.collection_types);
  };

  const fetchCollectionMethods = async () => {
    const response = await apiGetCollectionMethods();
    setCollectionMethods(response.data.collection_methods);
  };

  useEffect(() => {
    fetchCollectionType();
  }, []);

  useEffect(() => {
    fetchCollectionMethods();
  }, []);

  const onFormSubmitContact = (
    values: ProfileFormModel,
    setSubmitting: (isSubmitting: boolean) => void
  ) => {
    // values.nextContactDate + " " + values.nextContactTime  to datetime
    var sendValues = values;

    dispatch(postPayments(sendValues))
      .then(async (response) => {
        //reset form
        if (response.payload.id) {
          values = initialValues;

          toast.push(
            <Notification title={t("payments.addSuccess")} type="success" />,
            {
              placement: "top-center",
            }
          );

          // The payment is saved; a failed upload never undoes it.
          if (receipt) {
            try {
              await paymentAttachmentsApi.upload(
                String((response.payload as { id: string }).id),
                receipt,
                newIdempotencyKey()
              );
              setReceipt(null);
            } catch (error) {
              setReceipt(null);
              toast.push(
                <Notification
                  title={t("p7.payAtt.uploadFailedAfterSave")}
                  type="warning"
                  duration={9000}
                >
                  {describeReceiptError(error, t)} {t("p7.payAtt.attachLater")}
                </Notification>,
                {
                  placement: "top-center",
                }
              );
            }
          }

          dispatch(getPayments(sendValues?.clint_id));
        } else {
          toast.push(
            <Notification title={t("payments.addFail")} type="danger">
              {response.payload.message}
            </Notification>,
            {
              placement: "top-center",
            }
          );
        }
      })
      .catch((error) => {
        toast.push(
          <Notification title={t("payments.addFail")} type="danger" />,
          {
            placement: "top-center",
          }
        );
      })
      .finally(() => {
        setSubmitting(false);
      });
  };

  return (
    <>
      <Formik
        enableReinitialize
        initialValues={initialValues}
        validationSchema={validationSchema}
        onSubmit={(values, { setSubmitting }) => {
          setSubmitting(true);
          setTimeout(() => {
            onFormSubmitContact(values, setSubmitting);
          }, 1000);
        }}
      >
        {({ values, touched, errors, isSubmitting, resetForm }) => {
          const validatorProps = { touched, errors };
          return (
            <Form>
              <FormContainer>
                <FormDesription title={t("payments.add")} />
                <div className="grid grid-cols-2 gap-4">
                  <FormRow
                    border={false}
                    name="amount"
                    label={t("payments.amount")}
                    {...validatorProps}
                  >
                    <Field
                      border={false}
                      type="number"
                      autoComplete="off"
                      name="amount"
                      placeholder={t("payments.amount")}
                      component={Input}
                    />
                  </FormRow>
                  <FormRow
                    border={false}
                    name="payment_date"
                    label={t("payments.date")}
                    {...validatorProps}
                  >
                    <Field
                      border={false}
                      type="date"
                      autoComplete="off"
                      name="payment_date"
                      placeholder={t("comments.date")}
                      component={Input}
                    />
                  </FormRow>

                  <FormRow
                    border={false}
                    name="collection_type_id"
                    label={t("payments.type")}
                    {...validatorProps}
                  >
                    <Field name="collection_type_id">
                      {({ field, form }: FieldProps) => (
                        <Select
                          field={field}
                          form={form}
                          options={collectionTypeOptions}
                          value={collectionTypeOptions?.filter(
                            (category) =>
                              category.value === values.collection_type_id
                          )}
                          onChange={(option) => {
                            form.setFieldValue(field.name, option?.value);
                          }}
                          placeholder={t("select")}
                        />
                      )}
                    </Field>
                  </FormRow>

                  <FormRow
                    border={false}
                    name="collection_method_id"
                    label={t("payments.method")}
                    {...validatorProps}
                  >
                    <Field name="collection_method_id">
                      {({ field, form }: FieldProps) => (
                        <Select
                          field={field}
                          form={form}
                          options={collectionMethodOptions}
                          value={collectionMethodOptions?.filter(
                            (category) =>
                              category.value === values.collection_method_id
                          )}
                          onChange={(option) => {
                            form.setFieldValue(field.name, option?.value);
                          }}
                          placeholder={t("select")}
                        />
                      )}
                    </Field>
                  </FormRow>
                </div>

                <div className="mt-4">
                  <div className="mb-2 text-[13.5px] font-semibold text-gray-700 dark:text-night-body">
                    {t("p7.payAtt.formLabel")}
                  </div>
                  <ReceiptPicker
                    file={receipt}
                    onChange={setReceipt}
                    disabled={
                      isSubmitting ||
                      !!(clint as { is_archive?: boolean } | undefined)?.is_archive ||
                      !!(clint as { is_locked?: boolean } | undefined)?.is_locked
                    }
                  />
                </div>

                <div className="mt-6 flex justify-end">
                  <Button
                    variant="solid"
                    size="sm"
                    loading={isSubmitting}
                    type="submit"
                    disabled={clint?.is_archive || clint?.is_locked}
                  >
                    {isSubmitting ? t("save") : t("save")}
                  </Button>
                </div>
              </FormContainer>
            </Form>
          );
        }}
      </Formik>

      <PaymentClient />
    </>
  );
};

export default Comments;
