import AdaptableCard from "@/components/shared/AdaptableCard";
import Input from "@/components/ui/Input";
import { FormItem } from "@/components/ui/Form";
import { Field, FormikErrors, FormikTouched } from "formik";
import { useTranslation } from "react-i18next";
import Select from "@/components/ui/Select";

type FormFields = {
  id: string;
  name: string;
};

type Fields = {
  touched: FormikTouched<FormFields>;
  errors: FormikErrors<FormFields>;
};

const Fields = (props: Fields) => {
  const { t } = useTranslation();
  const { type, touched, errors, branches, users, contracts, values } = props;

  const branchOtions = branches?.map((branch) => ({
    value: branch.id,
    label: branch.name,
  }));

  const userOtions = users?.map((user) => ({
    value: user.id,
    label: user.name,
    branch_id: user.branch_id,
  }));

  const contractOtions = contracts?.map((contract) => ({
    value: contract.id,
    label: contract.name,
  }));

  return (
    <AdaptableCard className="mb-4">
      <FormItem
        label={t("branch")}
        invalid={(errors.branch_id && touched.branch_id) as boolean}
        errorMessage={errors.branch_id}
      >
        <Field name="branch_id">
          {({ field, form }: FieldProps) => (
            <Select
              isDisabled={type === "edit" ? true : false}
              field={field}
              form={form}
              options={branchOtions}
              value={branchOtions?.filter(
                (category) => category.value === values.branch_id
              )}
              onChange={(option) =>
                form.setFieldValue(field.name, option?.value)
              }
            />
          )}
        </Field>
      </FormItem>

      <FormItem
        label={t("supervisors.name")}
        invalid={(errors.user_id && touched.user_id) as boolean}
        errorMessage={errors.user_id}
      >
        <Field name="user_id">
          {({ field, form }: FieldProps) => (
            <Select
              isDisabled={type === "edit" ? true : false}
              field={field}
              form={form}
              options={userOtions?.filter((data) =>
                values.branch_id ? data.branch_id === values.branch_id : true
              )}
              value={userOtions?.filter(
                (category) => category.value === values.user_id
              )}
              onChange={(option) =>
                form.setFieldValue(field.name, option?.value)
              }
            />
          )}
        </Field>
      </FormItem>

      <FormItem
        label={t("contracts.title")}
        invalid={(errors.contract_result && touched.contract_result) as boolean}
        errorMessage={errors.contract_result}
      >
        <Field name="contract_result">
          {({ field, form }: FieldProps) => (
            <Select
              isMulti={true}
              field={field}
              form={form}
              options={contractOtions}
              /*  value={contractOtions?.filter(
                (category) => category.value === values.contract_result
              )} */
              onChange={(option) => form.setFieldValue(field.name, option)}
            />
          )}
        </Field>
      </FormItem>

      <FormItem
        label={t("supervisors.collecters")}
        invalid={(errors.user_result && touched.user_result) as boolean}
        errorMessage={errors.user_result}
      >
        <Field name="user_result">
          {({ field, form }: FieldProps) => (
            <Select
              isMulti={true}
              field={field}
              form={form}
              options={userOtions?.filter(
                (data) => data.value !== values.user_id
              )}
              /* value={contractOtions?.filter(
                (category) => category.value === values.user_result
              )} */
              onChange={(option) => form.setFieldValue(field.name, option)}
            />
          )}
        </Field>
      </FormItem>
    </AdaptableCard>
  );
};

export default Fields;
