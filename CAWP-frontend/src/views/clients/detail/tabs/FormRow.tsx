import classNames from "classnames";
import { FormItem } from "@/components/ui/Form";
import type { PropsWithChildren } from "react";
import type { FormikTouched, FormikErrors } from "formik";

type FormRow<T> = PropsWithChildren<{
  label: string;
  errors: FormikErrors<T>;
  touched: FormikTouched<T>;
  name: keyof T;
  border?: boolean;
  alignCenter?: boolean;
}>;

const FormRow = <T extends Record<string, unknown>>(props: FormRow<T>) => {
  const {
    label,
    children,
    errors,
    touched,
    name,
    border = true,
    alignCenter = true,
  } = props;

  return (
    <div
      className={classNames(
        "grid md:grid-cols-4 gap-2 py-4",
        border && "border-b border-gray-200 dark:border-gray-600",
        alignCenter && "items-center"
      )}
    >
      <div className="text-[13.5px] font-extrabold text-navy-600">{label}</div>
      <div className="col-span-3">
        <FormItem
          className="mb-0 max-w-[700px]"
          invalid={(errors[name] && touched[name]) as boolean}
          errorMessage={errors[name] as string}
        >
          {children}
        </FormItem>
      </div>
    </div>
  );
};

export default FormRow;
