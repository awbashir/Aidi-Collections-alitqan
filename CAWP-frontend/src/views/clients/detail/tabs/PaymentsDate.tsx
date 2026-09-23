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
  postPaymentDate,
  getPaymentDate,
} from "../store";
import { init, use } from "i18next";
import TimeLineComment from "../components/Timeline";
import PaymentDateClient from "../components/PaymentDateClientTable";

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
    payment_date: Yup.string().required(t("requiredField")),
    amount: Yup.string().required(t("requiredField")),
  });
  const clint = useAppSelector(
    (state) => state.crmCustomerDetails?.data?.clientData
  );

  const userId = useAppSelector((state) => state.auth.user.id);

  const initialValues = {
    clint_id: clint?.id,
    user_id: userId,
    amount: "",
    payment_date: "",
  };

  const [collectionType, setCollectionType] = useState([]);
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

    dispatch(postPaymentDate(sendValues))
      .then((response) => {
        //reset form
        if (response.payload.id) {
          values = initialValues;
          dispatch(getPaymentDate(clint?.id));

          toast.push(
            <Notification
              title={t("paymentsDate.addSuccess")}
              type="success"
            />,
            {
              placement: "top-center",
            }
          );
        } else {
          toast.push(
            <Notification title={t("paymentsDate.addFail")} type="danger">
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
          <Notification title={t("paymentsDate.addFail")} type="danger" />,
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
                <FormDesription title={t("paymentsDate.add")} />
                <div className="grid grid-cols-2 gap-4">
                  <FormRow
                    border={false}
                    name="amount"
                    label={t("paymentsDate.amount")}
                    {...validatorProps}
                  >
                    <Field
                      border={false}
                      type="number"
                      autoComplete="off"
                      name="amount"
                      placeholder={t("paymentsDate.amount")}
                      component={Input}
                    />
                  </FormRow>
                  <FormRow
                    border={false}
                    name="payment_date"
                    label={t("paymentsDate.date")}
                    {...validatorProps}
                  >
                    <Field
                      border={false}
                      type="date"
                      autoComplete="off"
                      name="payment_date"
                      placeholder={t("paymentsDate.date")}
                      component={Input}
                    />
                  </FormRow>
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

      <PaymentDateClient />
    </>
  );
};

export default Comments;
