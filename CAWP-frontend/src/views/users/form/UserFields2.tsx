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
  const { values, touched, errors, nationalities } = props;

  const nationalityOtions = nationalities.map((national) => ({
    value: national.id,
    label: national.name,
  }));

  return (
    <AdaptableCard className="mb-4">
      <FormItem
        label={t("users.frist_name")}
        invalid={(errors.frist_name && touched.frist_name) as boolean}
        errorMessage={errors.frist_name}
      >
        <Field
          type="text"
          autoComplete="off"
          name="frist_name"
          placeholder={t("users.frist_name")}
          component={Input}
        />
      </FormItem>

      <FormItem
        label={t("users.last_name")}
        invalid={(errors.last_name && touched.last_name) as boolean}
        errorMessage={errors.last_name}
      >
        <Field
          type="text"
          autoComplete="off"
          name="last_name"
          placeholder={t("users.last_name")}
          component={Input}
        />
      </FormItem>
      <FormItem
        label={t("users.email")}
        invalid={(errors.email && touched.email) as boolean}
        errorMessage={errors.email}
      >
        <Field
          type="email"
          autoComplete="off"
          name="email"
          placeholder="Email"
          component={Input}
        />
      </FormItem>

      <FormItem
        label={t("identity")}
        invalid={(errors.identityNumber && touched.identityNumber) as boolean}
        errorMessage={errors.identityNumber}
      >
        <Field
          type="text"
          autoComplete="off"
          name="identityNumber"
          placeholder={t("identity")}
          component={Input}
        />
      </FormItem>
      <FormItem
        label={t("mobile")}
        invalid={(errors.mobile && touched.mobile) as boolean}
        errorMessage={errors.mobile}
      >
        <Field
          type="text"
          autoComplete="off"
          name="mobile"
          placeholder={t("mobile")}
          component={Input}
        />
      </FormItem>
      <FormItem
        label={t("nationality")}
        invalid={(errors.nationality_id && touched.nationality_id) as boolean}
        errorMessage={errors.nationality_id}
      >
        <Field name="nationality_id">
          {({ field, form }: FieldProps) => (
            <Select
              field={field}
              form={form}
              options={nationalityOtions}
              value={nationalityOtions.filter(
                (category) => category.value === values.nationality_id
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
