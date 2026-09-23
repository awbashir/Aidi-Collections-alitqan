import AdaptableCard from "@/components/shared/AdaptableCard";
import Input from "@/components/ui/Input";
import { FormItem } from "@/components/ui/Form";
import { Field, FormikErrors, FormikTouched } from "formik";
import { useTranslation } from "react-i18next";
import Select from "@/components/ui/Select";


type FormFields = {
  id: string;
  name: string;
  monthly_target_percent?: number;
  monthly_target_is_percentage: boolean;
  monthly_target_amount?: number;

};

type Fields = {
  touched: FormikTouched<FormFields>;
  errors: FormikErrors<FormFields>;
};

const Fields = (props: Fields) => {
  const { t } = useTranslation();
  const { touched, errors , values } = props;

  return (
    <AdaptableCard className="mb-4">
      <FormItem
        label={t("contracts.name")}
        invalid={(errors.name && touched.name) as boolean}
        errorMessage={errors.name}
      >
        <Field
          type="text"
          autoComplete="new-password"
          name="name"
          placeholder={t("contracts.name")}
          component={Input}
        />
      </FormItem>

      <div className="grid grid-cols-3 gap-4">
        <FormItem
          label={t("monthly_target_is_percentage")}
          invalid={(errors.monthly_target_is_percentage && touched.monthly_target_is_percentage) as boolean}
          errorMessage={errors.monthly_target_is_percentage}
        >
          <Field name="monthly_target_is_percentage">
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
                ].filter(option => option.value === values.monthly_target_is_percentage)}
                onChange={(option) =>
                  form.setFieldValue(field.name, option?.value || false)
                }
              />
            )}
          </Field>
        </FormItem>

        <FormItem
          label={t("monthly_target_amount")}
          invalid={(errors.monthly_target_amount && touched.monthly_target_amount) as boolean}
          errorMessage={errors.monthly_target_amount}
        >
          <Field
            type="number"
            autoComplete="off"
            name="monthly_target_amount"
            placeholder={t("monthly_target_amount")}
            component={Input}
          />
        </FormItem>

        <FormItem
          label={t("monthly_target_percent")}
          invalid={(errors.monthly_target_percent && touched.monthly_target_percent) as boolean}
          errorMessage={errors.monthly_target_percent}
        >
          <Field
            type="number"
            autoComplete="off"
            name="monthly_target_percent"
            placeholder={t("monthly_target_percent")}
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

export default Fields;
