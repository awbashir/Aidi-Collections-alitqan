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
  target_is_percentage: boolean;
  target_amount: string;
  target_percent: string;
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

const UserFields3 = (props: UserFields) => {
  const { t } = useTranslation();
  const { values, touched, errors, contracts } = props;

  const contractOtions = contracts.map((contract) => ({
    value: contract.id,
    label: contract.name,
  }));

  return (
    <AdaptableCard className="mb-4">
      {/* <FormItem
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
 */}
      {/*  <FormItem
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
      </FormItem> */}
      {/*  <FormItem
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
      </FormItem> */}

      {/*   <FormItem
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
      </FormItem> */}
      <FormItem
        label={t("users.extensionNo")}
        invalid={(errors.extension && touched.extension) as boolean}
        errorMessage={errors.extension}
      >
        <Field
          type="number"
          autoComplete="off"
          name="extension"
          placeholder={t("users.extensionNo")}
          component={Input}
        />
      </FormItem>
      <FormItem
        label={t("contracts.title")}
        invalid={(errors.contract_id && touched.contract_id) as boolean}
        errorMessage={errors.contract_id}
      >
       <Field name="contract_id">
  {({ field, form }: FieldProps) => (
    <Select
      field={field}
      form={form}
      isClearable
      options={contractOtions}
      value={contractOtions.filter(
        (category) => category.value === values.contract_id
      )}
      onChange={(option) =>
        form.setFieldValue(field.name, option?.value || null)
      }
    />
  )}
</Field>
      </FormItem>
      <div className="grid grid-cols-3 gap-4">
        <FormItem
          label={t("targetIsPercentage")}
          invalid={(errors.target_is_percentage && touched.target_is_percentage) as boolean}
          errorMessage={errors.target_is_percentage}
        >
          <Field name="target_is_percentage">
            {({ field, form }: FieldProps) => (
              <Select
                field={field}
                form={form}
                placeholder={t("select")}
                options={[
                  { value: true, label: t("yes") },
                  { value: false, label: t("no") }
                ]}
                value={[
                  { value: true, label: t("yes") },
                  { value: false, label: t("no") }
                ].filter(option => option.value === values.target_is_percentage)}
                onChange={(option) =>
                  form.setFieldValue(field.name, option?.value || false)
                }
              />
            )}
          </Field>
        </FormItem>

        <FormItem
          label={t("targetAmount")}
          invalid={(errors.target_amount && touched.target_amount) as boolean}
          errorMessage={errors.target_amount}
        >
          <Field
            type="number"
            autoComplete="off"
            name="target_amount"
            placeholder={t("targetAmount")}
            component={Input}
          />
        </FormItem>

        <FormItem
          label={t("targetPercent")}
          invalid={(errors.target_percent && touched.target_percent) as boolean}
          errorMessage={errors.target_percent}
        >
          <Field
            type="number"
            autoComplete="off"
            name="target_percent"
            placeholder={t("targetPercent")}
            min={0}
            max={100}
            step={0.01}
            component={Input}
          />
        </FormItem>
      </div>

      
    </AdaptableCard>
  );
};

export default UserFields3;
