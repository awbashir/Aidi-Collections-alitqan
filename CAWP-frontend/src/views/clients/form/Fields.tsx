import AdaptableCard from "@/components/shared/AdaptableCard";
import Input from "@/components/ui/Input";
import { FormItem } from "@/components/ui/Form";
import { Field, FormikErrors, FormikTouched } from "formik";
import { useTranslation } from "react-i18next";

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
  const { touched, errors } = props;

  return (
    <AdaptableCard className="mb-4">
      <FormItem
        label={t("clients.name")}
        invalid={(errors.name && touched.name) as boolean}
        errorMessage={errors.name}
      >
        <Field
          type="text"
          autoComplete="new-password"
          name="name"
          placeholder={t("clients.name")}
          component={Input}
        />
      </FormItem>
    </AdaptableCard>
  );
};

export default Fields;
