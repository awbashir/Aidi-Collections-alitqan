import Input from "@/components/ui/Input";
import Avatar from "@/components/ui/Avatar";
import Upload from "@/components/ui/Upload";
import Button from "@/components/ui/Button";
import Select from "@/components/ui/Select";
import Switcher from "@/components/ui/Switcher";
import Notification from "@/components/ui/Notification";
import toast from "@/components/ui/toast";
import { FormContainer } from "@/components/ui/Form";
import FormDesription from "./FormDesription";
import FormRow from "./FormRow";
import { Field, Form, Formik } from "formik";
import { components } from "react-select";
import {
  HiOutlineUserCircle,
  HiOutlineMail,
  HiOutlineBriefcase,
  HiOutlineUser,
  HiCheck,
  HiOutlineGlobeAlt,
} from "react-icons/hi";
import * as Yup from "yup";
import type { OptionProps, ControlProps } from "react-select";
import type { FormikProps, FieldInputProps, FieldProps } from "formik";
import { useTranslation } from "react-i18next";
import { putClient, postClintContactTypes, useAppDispatch } from "../store";
import { apiGetListClintContactTypes } from "@/services/ClientsService";
import { useEffect, useState } from "react";
import Card from "@/components/ui/Card";

export type ProfileFormModel = {
  name: string;
  email: string;
  title: string;
  avatar: string;
  timeZone: string;
  lang: string;
  syncData: boolean;
};

type ProfileProps = {
  data?: ProfileFormModel;
};

const { Control } = components;

const validationSchema = Yup.object().shape({
  name: Yup.string().required("هذا الحقل مطلوب"),
  identityNumber: Yup.string().required("هذا الحقل مطلوب"),
  amount: Yup.string().required("هذا الحقل مطلوب"),
});

const Profile = ({
  data = {
    name: "",
    email: "",
    title: "",
    avatar: "",
    timeZone: "",
    lang: "",
    syncData: false,
  },
  tap,
}: ProfileProps) => {
  const onSetFormFile = (
    form: FormikProps<ProfileFormModel>,
    field: FieldInputProps<ProfileFormModel>,
    file: File[]
  ) => {
    form.setFieldValue(field.name, URL.createObjectURL(file[0]));
  };

  const [clint, setClint] = useState(data);
  const dispatch = useAppDispatch();

  const onFormSubmit = (
    values: ProfileFormModel,
    setSubmitting: (isSubmitting: boolean) => void
  ) => {
    let newValues = { ...values, attribute: clint.attribute };
    dispatch(putClient(newValues))
      .then(() => {
        toast.push(
          <Notification title={t("clients.updatedSuccess")} type="success" />,
          {
            placement: "top-center",
          }
        );

        tap("details");
      })
      .catch((error) => {
        toast.push(
          <Notification title={t("clients.updatedFail")} type="danger" />,
          {
            placement: "top-center",
          }
        );
      })
      .finally(() => {
        setSubmitting(false);
      });
  };

  const { t } = useTranslation();

  const [contractTypes, setContractTypes] = useState<OptionProps[]>([]);
  useEffect(() => {
    apiGetListClintContactTypes().then((response) => {
      setContractTypes(response.data.clint_contact_types);
    });
  }, []);

  //contractTypeOptions
  const contractTypeOptions = contractTypes?.map((contract) => ({
    value: contract.id,
    label: t(contract.name),
    name: contract.name,
  }));

  const initialValuesContacts = {
    contact_status: "new",
    identityNumber: clint.identityNumber,
    contact_type_id: "",
    contact: "",
  };

  const ContactsDateSchema = Yup.object().shape({
    contact_type_id: Yup.string().required(t("requiredField")),
    contact: Yup.string().required(t("requiredField")),
  });

  const onFormSubmitContact = (
    values: ProfileFormModel,
    setSubmitting: (isSubmitting: boolean) => void
  ) => {
    dispatch(postClintContactTypes(values))
      .then(() => {
        toast.push(
          <Notification title={t("clients.updatedSuccess")} type="success" />,
          {
            placement: "top-center",
          }
        );

        tap("details");
      })
      .catch((error) => {
        toast.push(
          <Notification title={t("clients.updatedFail")} type="danger" />,
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
        initialValues={data}
        validationSchema={validationSchema}
        onSubmit={(values, { setSubmitting }) => {
          setSubmitting(true);
          setTimeout(() => {
            onFormSubmit(values, setSubmitting);
          }, 1000);
        }}
      >
        {({ values, touched, errors, isSubmitting, resetForm }) => {
          const validatorProps = { touched, errors };
          return (
            <Card className="my-3">
              <Form>
                <FormContainer>
                  <FormDesription title={t("clients.updateData")} />
                  <div className="grid grid-cols-2 gap-4">
                    <FormRow
                      border={false}
                      name="accountNumber"
                      label={t("clients.accountNumber")}
                      {...validatorProps}
                    >
                      <Field
                        size="sm"
                        type="text"
                        autoComplete="off"
                        name="accountNumber"
                        placeholder={t("clients.accountNumber")}
                        component={Input}
                        readOnly
                      />
                    </FormRow>
                    <FormRow
                      border={false}
                      name="name"
                      label={t("clients.name")}
                      {...validatorProps}
                    >
                      <Field
                        size="sm"
                        type="text"
                        autoComplete="off"
                        name="name"
                        placeholder={t("clients.name")}
                        component={Input}
                      />
                    </FormRow>

                    <FormRow
                      border={false}
                      name="identityNumber"
                      label={t("clients.identityNumber")}
                      {...validatorProps}
                    >
                      <Field
                        size="sm"
                        border={false}
                        type="text"
                        autoComplete="off"
                        name="identityNumber"
                        placeholder={t("clients.identityNumber")}
                        component={Input}
                      />
                    </FormRow>
                    <FormRow
                      border={false}
                      name="amount"
                      label={t("amount")}
                      {...validatorProps}
                    >
                      <Field
                        size="sm"
                        type="number"
                        autoComplete="off"
                        name="amount"
                        placeholder={t("amount")}
                        component={Input}
                      />
                    </FormRow>
                  </div>

                  <FormDesription
                    className="mt-8"
                    title={t("extraInformation")}
                  />
                  <div className="grid grid-cols-2 gap-4">
                    {clint?.attribute
                      ? Object.entries(clint?.attribute).map(([key, value]) => {
                          var tvalue = value ? value : "";
                          return value ? (
                            <FormRow
                              border={false}
                              name={key}
                              label={key}
                              {...validatorProps}
                            >
                              <Field
                                size="sm"
                                type="text"
                                value={clint?.attribute[key] || ""}
                                name={key}
                                onChange={(e) => {
                                  setClint({
                                    ...clint,
                                    attribute: {
                                      ...clint.attribute,
                                      [key]: e.target.value,
                                    },
                                  });
                                }}
                                component={Input}
                              />
                            </FormRow>
                          ) : (
                            ""
                          );
                        })
                      : ""}
                  </div>

                  <div className="mt-6 flex justify-end">
                    <Button
                      variant="solid"
                      loading={isSubmitting}
                      type="submit"
                      disabled={clint?.is_archive || clint?.is_locked}
                    >
                      {isSubmitting ? t("save") : t("save")}
                    </Button>
                  </div>
                </FormContainer>
              </Form>
            </Card>
          );
        }}
      </Formik>

      <Formik
        enableReinitialize
        initialValues={initialValuesContacts}
        validationSchema={ContactsDateSchema}
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
            <Card className="my-3">
              <Form>
                <FormContainer>
                  <FormDesription
                    title={t("clients.addContact")}
                    className="mt-5"
                  />
                  <div className="grid grid-cols-2 gap-4">
                    <FormRow
                      border={false}
                      name="contact_type_id"
                      label={t("clients.contactType")}
                      {...validatorProps}
                    >
                      <Field name="contact_type_id">
                        {({ field, form }: FieldProps) => (
                          <Select
                            field={field}
                            form={form}
                            options={contractTypeOptions}
                            value={contractTypeOptions?.filter(
                              (category) =>
                                category.value === values.contact_type_id
                            )}
                            onChange={(option) =>
                              form.setFieldValue(field.name, option?.value)
                            }
                            placeholder={t("select")}
                          />
                        )}
                      </Field>
                    </FormRow>
                    <FormRow
                      border={false}
                      name="contact"
                      label={t("clients.contact")}
                      {...validatorProps}
                    >
                      <Field
                        size="sm"
                        type={
                          contractTypeOptions.find(
                            (x) => x.value == values.contact_type_id
                          )?.name === "email"
                            ? "email"
                            : "number"
                        }
                        autoComplete="off"
                        name="contact"
                        placeholder={t("clients.contact")}
                        component={Input}
                      />
                    </FormRow>
                  </div>

                  <div className="mt-6 flex justify-end">
                    <Button
                      variant="solid"
                      loading={isSubmitting}
                      type="submit"
                      disabled={clint?.is_archive || clint?.is_locked}
                    >
                      {isSubmitting ? t("save") : t("save")}
                    </Button>
                  </div>
                </FormContainer>
              </Form>
            </Card>
          );
        }}
      </Formik>
    </>
  );
};

export default Profile;
