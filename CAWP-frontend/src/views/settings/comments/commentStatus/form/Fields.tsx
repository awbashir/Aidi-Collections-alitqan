import { useEffect, useMemo, useState } from "react";
import classNames from "classnames";
import AdaptableCard from "@/components/shared/AdaptableCard";
import Input from "@/components/ui/Input";
import { FormItem } from "@/components/ui/Form";
import { Field, FormikErrors, FormikTouched } from "formik";
import type { FieldProps } from "formik";
import { useTranslation } from "react-i18next";
import Select from "@/components/ui/Select";
import { apiGetCommentStatus } from "@/services/ClientsService";
import { isMainStatus } from "@/utils/commentStatusTree";

/** White first: the default colour of a new status (customer decision, 0068). */
export const STATUS_PALETTE = [
  "#FFFFFF",
  "#8A929F",
  "#128A5C",
  "#2F5FD6",
  "#5B93C9",
  "#C86A00",
  "#B3261E",
  "#8E86B8",
];
export const DEFAULT_STATUS_COLOR = "#FFFFFF";

type Named = { id: string; name: string };

export type StatusFormValues = {
  id?: string;
  name: string;
  is_active: boolean | string;
  contract_id: string | null;
  CommentReachability_id: string | null;
  color: string;
  parent_id: string | null;
};

type StatusRow = Named & {
  contract_id?: string | null;
  CommentReachability_id?: string | null;
  parent_id?: string | null;
  deleted_at?: string | null;
};

type FieldsProps = {
  values: StatusFormValues;
  touched: FormikTouched<StatusFormValues>;
  errors: FormikErrors<StatusFormValues>;
  contracts?: Named[];
  reachability?: Named[];
};

const Fields = (props: FieldsProps) => {
  const { t } = useTranslation();
  const { values, touched, errors, contracts, reachability } = props;

  // every live status, for the parent select (main statuses only, one level)
  const [statuses, setStatuses] = useState<StatusRow[]>([]);
  const [statusesLoading, setStatusesLoading] = useState(true);
  useEffect(() => {
    let alive = true;
    apiGetCommentStatus<{ comment_statuses?: StatusRow[] }, Record<string, unknown>>({})
      .then((r) => alive && setStatuses(r.data?.comment_statuses || []))
      .catch(() => undefined)
      .finally(() => alive && setStatusesLoading(false));
    return () => {
      alive = false;
    };
  }, []);

  const contractOtions = (contracts || []).map((contract) => ({
    value: contract.id,
    label: contract.name,
  }));

  const reachabilityOtions = (reachability || []).map((reach) => ({
    value: reach.id,
    label: reach.name,
  }));

  const statusOtions = [
    { value: true, label: t("active_") },
    { value: false, label: t("inactive_") },
  ];

  const parentOptions = useMemo(
    () =>
      statuses
        .filter((s) => isMainStatus(s) && s.id !== values.id)
        .map((s) => ({
          value: s.id,
          label: s.name,
          contract_id: s.contract_id ?? null,
          reach: s.CommentReachability_id ?? null,
        })),
    [statuses, values.id]
  );
  // a status with sub-statuses cannot become one (one level only)
  const hasChildren =
    !!values.id && statuses.some((s) => s.parent_id === values.id);
  const parent = parentOptions.find((p) => p.value === values.parent_id);
  const inherited = !!values.parent_id;

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
        label={t("p7.status.parent")}
        extra={
          <span className="ms-1 text-[12px] text-gray-500">
            {t("p7.status.optional")}
          </span>
        }
      >
        <Field name="parent_id">
          {({ field, form }: FieldProps) => (
            <div data-testid="p7-status-parent">
              <Select
                field={field}
                form={form}
                isClearable
                isLoading={statusesLoading}
                isDisabled={hasChildren}
                options={parentOptions}
                value={parentOptions.filter((o) => o.value === values.parent_id)}
                onChange={(option) => {
                  form.setFieldValue(field.name, option?.value ?? null);
                  if (option) {
                    // a sub-status always takes its main status's contract and reachability
                    form.setFieldValue("contract_id", option.contract_id);
                    form.setFieldValue("CommentReachability_id", option.reach);
                  }
                }}
                placeholder={t("p7.status.noParent")}
              />
            </div>
          )}
        </Field>
        <div className="mt-1 text-[12px] text-gray-600">
          {hasChildren
            ? t("p7.status.hasChildrenHint")
            : t("p7.status.parentHint")}
        </div>
      </FormItem>

      <FormItem
        label={t("contracts.contract")}
        invalid={(errors.contract_id && touched.contract_id) as boolean}
        errorMessage={errors.contract_id}
      >
        <Field name="contract_id">
          {({ field, form }: FieldProps) => (
            <div data-testid="p7-status-contract">
              <Select
                field={field}
                form={form}
                isClearable
                isDisabled={inherited}
                options={contractOtions}
                value={contractOtions.filter(
                  (category) => category.value === values.contract_id
                )}
                onChange={(option) =>
                  form.setFieldValue(field.name, option?.value ?? null)
                }
                placeholder={t("p7.status.generalAll")}
              />
            </div>
          )}
        </Field>
        <div className="mt-1 text-[12px] text-gray-600">
          {inherited
            ? t("p7.status.inheritedHint", { name: parent?.label ?? "" })
            : t("p7.status.contractHint")}
        </div>
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
              isDisabled={inherited}
              options={reachabilityOtions}
              value={reachabilityOtions.filter(
                (category) => category.value === values.CommentReachability_id
              )}
              onChange={(option) =>
                form.setFieldValue(field.name, option?.value ?? null)
              }
              placeholder={t("select")}
            />
          )}
        </Field>
      </FormItem>

      <FormItem
        label={t("status")}
        invalid={(errors.is_active && touched.is_active) as boolean}
        errorMessage={errors.is_active as string}
      >
        <Field name="is_active">
          {({ field, form }: FieldProps) => (
            <Select
              field={field}
              form={form}
              options={statusOtions}
              value={statusOtions.filter(
                (category) => category.value === values.is_active
              )}
              onChange={(option) =>
                form.setFieldValue(field.name, option?.value)
              }
              placeholder={t("select")}
            />
          )}
        </Field>
      </FormItem>

      {/* colour: a small palette (white first, the default) + the custom picker */}
      <FormItem
        label={t("color")}
        invalid={(errors.color && touched.color) as boolean}
        errorMessage={errors.color}
      >
        <Field name="color">
          {({ form }: FieldProps) => {
            const current = (values.color || DEFAULT_STATUS_COLOR).toUpperCase();
            return (
              <div
                className="flex flex-wrap items-center gap-2"
                role="radiogroup"
                aria-label={String(t("color"))}
                data-testid="p7-status-color"
              >
                {STATUS_PALETTE.map((c) => (
                  <button
                    key={c}
                    type="button"
                    role="radio"
                    aria-checked={current === c}
                    title={c === "#FFFFFF" ? String(t("p7.status.white")) : c}
                    aria-label={c === "#FFFFFF" ? String(t("p7.status.white")) : c}
                    onClick={() => form.setFieldValue("color", c)}
                    className={classNames(
                      "h-8 w-8 rounded-full border focus:outline-none focus-visible:ring-2 focus-visible:ring-azure-500",
                      current === c
                        ? "border-azure-500 ring-2 ring-azure-500 ring-offset-2 dark:ring-offset-night-card"
                        : "border-gray-300 dark:border-night-border"
                    )}
                    style={{ backgroundColor: c }}
                  />
                ))}
                <label className="ms-2 inline-flex items-center gap-2 text-[12.5px] text-gray-600">
                  {t("p7.status.customColor")}
                  <input
                    type="color"
                    value={current.length === 7 ? current.toLowerCase() : "#ffffff"}
                    onChange={(e) =>
                      form.setFieldValue("color", e.target.value.toUpperCase())
                    }
                    className="h-8 w-10 cursor-pointer rounded border border-gray-300 bg-transparent p-0.5 dark:border-night-border"
                  />
                  <span className="tabular-nums" dir="ltr">
                    {current}
                  </span>
                </label>
              </div>
            );
          }}
        </Field>
      </FormItem>
    </AdaptableCard>
  );
};

export default Fields;
