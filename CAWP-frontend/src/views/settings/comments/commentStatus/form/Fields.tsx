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
  const { values, touched, errors, contracts, reachability } = props;

  const contractOtions = contracts?.map((contract) => ({
    value: contract.id,
    label: contract.name,
  }));

  const reachabilityOtions = reachability?.map((reach) => ({
    value: reach.id,
    label: reach.name,
  }));

  const statusOtions = [
    { value: true, label: t("active_") },
    { value: false, label: t("inactive_") },
  ];

  return (
    <AdaptableCard className="mb-4">
      <FormItem
        label={t("comments.status")}
        invalid={(errors.name && touched.name) as boolean}
        errorMessage={errors.name}
      >
        <Field
          type="text"
          autoComplete="new-password"
          name="name"
          placeholder={t("comments.status")}
          component={Input}
        />
      </FormItem>

      <FormItem
        label={t("contracts.contract")}
        invalid={(errors.contract_id && touched.contract_id) as boolean}
        errorMessage={errors.contract_id}
      >
        <Field name="contract_id">
          {({ field, form }: FieldProps) => (
            <Select
              field={field}
              form={form}
              options={contractOtions}
              value={contractOtions?.filter(
                (category) => category.value === values.contract_id
              )}
              onChange={(option) =>
                form.setFieldValue(field.name, option?.value)
              }
              placeholder={t("select")}
            />
          )}
        </Field>
      </FormItem>

      <FormItem
        label={t("reachability")}
        invalid={
          (errors.CommentReachability_id &&
            touched.CommentReachability_id) as boolean
        }
        errorMessage={errors.CommentReachability_id}
      >
        <Field name="CommentReachability_id">
          {({ field, form }: FieldProps) => (
            <Select
              field={field}
              form={form}
              options={reachabilityOtions}
              value={reachabilityOtions?.filter(
                (category) => category.value === values.CommentReachability_id
              )}
              onChange={(option) =>
                form.setFieldValue(field.name, option?.value)
              }
              placeholder={t("select")}
            />
          )}
        </Field>
      </FormItem>

      <FormItem
        label={t("status")}
        invalid={(errors.is_active && touched.is_active) as boolean}
        errorMessage={errors.is_active}
      >
        <Field name="is_active">
          {({ field, form }: FieldProps) => (
            <Select
              field={field}
              form={form}
              options={statusOtions}
              value={statusOtions?.filter(
                (category) => category.value === values.is_active
              )}
              onChange={(option) =>
                form.setFieldValue(field.name, option?.value)
              }
              placeholder={t("select")}
            />
          )}
        </Field>

        {/* color field */}
        <FormItem
          label={t("color")}
          invalid={(errors.color && touched.color) as boolean}
          errorMessage={errors.color}
        >
          <Field
            type="color"
            autoComplete="new-password"
            name="color"
            placeholder={t("color")}
            component={Input}
          />
        </FormItem>
      </FormItem>
    </AdaptableCard>
  );
};

export default Fields;
