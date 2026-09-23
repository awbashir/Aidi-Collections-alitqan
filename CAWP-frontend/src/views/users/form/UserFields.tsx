import AdaptableCard from "@/components/shared/AdaptableCard";
import Input from "@/components/ui/Input";
import { FormItem } from "@/components/ui/Form";
import { Field, FormikErrors, FormikTouched, FieldProps } from "formik";
import { useTranslation } from "react-i18next";
import Select from "@/components/ui/Select";

type nationality = {
  id: string;
  is_active: boolean;
  name: string;
  created_at: Date;
  updated_at: Date;
};

type FormFieldsName = {
  frist_name: string;
  last_name: string;
  email: string;
  identityNumber: string;
  mobile: string;
  nationality_id: string;
};

type UserFields = {
  touched: FormikTouched<FormFieldsName>;
  errors: FormikErrors<FormFieldsName>;
};

//nationalities as option
type nationalityOption = {
  value: string;
  label: string;
};

const UserFields = (props: UserFields) => {
  const { t } = useTranslation();
  const { values, touched, errors, nationalities, roles, branches } = props;

  const nationalityOtions = nationalities?.map((national) => ({
    value: national.id,
    label: national.name,
  }));

  const branchOtions = branches?.map((branch) => ({
    value: branch.id,
    label: branch.name,
  }));

  const roleOtions = roles?.map((role) => ({
    value: role.id,
    label: role.name,
  }));

  return (
    <AdaptableCard className="mb-4">
      <FormItem
        label={t("users.name")}
        invalid={(errors.user_name && touched.user_name) as boolean}
        errorMessage={errors.user_name}
      >
        <Field
          type="text"
          autoComplete="new-password"
          name="user_name"
          placeholder={t("users.name")}
          component={Input}
        />
      </FormItem>

      <FormItem
        label={t("password")}
        invalid={(errors.password && touched.password) as boolean}
        errorMessage={errors.password}
      >
        <Field
          type="password"
          autoComplete="new-password"
          name="password"
          placeholder={t("password")}
          component={Input}
        />
      </FormItem>
      <FormItem
        label={t("branch")}
        invalid={(errors.branch_id && touched.branch_id) as boolean}
        errorMessage={errors.branch_id}
      >
        <Field name="branch_id">
          {({ field, form }: FieldProps) => (
            <Select
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
        label={t("permissions")}
        invalid={(errors.role_id && touched.role_id) as boolean}
        errorMessage={errors.role_id}
      >
        <Field name="role_id">
          {({ field, form }: FieldProps) => (
            <Select
              field={field}
              form={form}
              options={roleOtions}
              value={roleOtions?.filter(
                (category) => category.value === values.role_id
              )}
              onChange={(option) =>
                form.setFieldValue(field.name, option?.value)
              }
            />
          )}
        </Field>
      </FormItem>
    </AdaptableCard>
  );
};

export default UserFields;
