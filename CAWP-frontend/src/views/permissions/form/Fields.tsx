import AdaptableCard from "@/components/shared/AdaptableCard";
import Input from "@/components/ui/Input";
import { FormItem } from "@/components/ui/Form";
import { Field, FormikErrors, FormikTouched } from "formik";
import { useTranslation } from "react-i18next";
import Select from "@/components/ui/Select";
import CheckboxTree from "react-checkbox-tree";
//import "react-checkbox-tree/lib/react-checkbox-tree.css";
import { useState, useEffect, Children } from "react";
import { HiCheck } from "react-icons/hi";
import { HiOutlineFolder } from "react-icons/hi";
import { FaRegSquare } from "react-icons/fa6";
import { FaRegSquareCheck } from "react-icons/fa6";
import { MdOutlineInsertDriveFile } from "react-icons/md";
import { MdOutlineFolder } from "react-icons/md";
import { FaRegFolderOpen } from "react-icons/fa";
import {
  PHASE1_PERMISSIONS,
  PHASE1_PERMISSION_GROUPS,
  phase1PermissionLabel,
} from "@/constants/permissions.phase1";

const CALLING_PERMISSION_KEYS = [
  "calls",
  "calls_make",
  "calls_manage",
  "calls_supervise",
  "campaigns",
  "campaigns_manage",
];

type FormFields = {
  id: string;
  name: string;
};

type Fields = {
  touched: FormikTouched<FormFields>;
  errors: FormikErrors<FormFields>;
};

const Fields = (props: Fields) => {
  const { t, i18n } = useTranslation();
  const { values, touched, errors, contracts, reachability } = props;

  /* The calling and campaign keys get their own top-level group, first in the tree
     (customer feedback 2026-10-21: under Workflow permissions › Daily operations nobody
     found them). Display only: the registry keeps their group. */
  const callingNode = {
    value: "calling_",
    label: t("p5.perm.calling"),
    children: CALLING_PERMISSION_KEYS.map((key) => ({
      value: key,
      label: phase1PermissionLabel(key, i18n.language),
    })),
  };

  /* Phase 1 keys (CAWP-API/core/permissions.py), grouped like the sidebar,
     labelled in the current language. */
  const phase1Node = {
    value: "phase1_",
    /* Phase 3: the node holds every registered new key; Phase 5 extends it to
       the report and calling keys, Phase 6 to the AI and quality keys (Phases 1–6). */
    label: t("p6.perm.section"),
    children: PHASE1_PERMISSION_GROUPS.map((group) => ({
      value: `phase1_${group}_`,
      label: t(`p1.perm.group.${group}`),
      children: PHASE1_PERMISSIONS.filter(
        (p) => p.group === group && !CALLING_PERMISSION_KEYS.includes(p.key)
      ).map(
        (p) => ({
          value: p.key,
          label: phase1PermissionLabel(p.key, i18n.language),
        })
      ),
    })),
  };

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

  const nodes = [
    {
      value: "all",
      label: "ـ",
      children: [
        callingNode,
        {
          value: "dashbord_",
          label: "لوحة التحكم",
          children: [
            { value: "dashbord", label: "عرض" },
            { value: "dashbord_admin", label: "مسؤول" },
            { value: "dashbord_user", label: "مستخدم" },
          ],
        },
        {
          value: "users_",
          label: "المستخدمين",

          children: [
            { value: "users", label: "عرض" },
            { value: "users_details", label: "تفاصيل المستخدم" },
            { value: "users_add", label: "اضافة" },
            { value: "users_edit", label: "تعديل" },
            { value: "users_delete", label: "حذف" },
            { value: "users_edit_general", label: "تعديل المعلومات العامة" },
            { value: "users_edit_target", label: "تعديل الهدف والتحويلة" },
          ],
        },
        {
          value: "contracts_",
          label: "العقود",
          children: [
            { value: "contracts", label: "عرض" },
            { value: "contracts_add", label: "اضافة" },
            { value: "contracts_edit", label: "تعديل" },
            { value: "contracts_delete", label: "حذف" },
          ],
        },
        {
          value: "portfolios_",
          label: "المحافظ",
          children: [
            { value: "portfolios", label: "عرض" },
            { value: "portfolios_add", label: "اضافة" },
            { value: "portfolios_edit", label: "تعديل" },
            { value: "portfolios_delete", label: "حذف" },
          ],
        },
        {
          value: "clints_",
          label: "العملاء",
          children: [
            { value: "clints", label: "عرض" },
            { value: "clints_add", label: "اضافة" },
            { value: "clints_edit", label: "تعديل" },
            { value: "clints_delete", label: "حذف" },
            { value: "clints_sepcifyUser", label: "تخصيص عملاء" },
            { value: "clints_move_archive", label: "نقل ارشيف" },
            { value: "clints_UpdateByExcel", label: "تحديث بالاكسل" },
            { value: "clints_details", label: "تفاصيل العميل" },
            { value: "clints_comments", label: "المتابعات" },
            { value: "clints_Payments", label: "السدادت" },
            { value: "clints_PayemntDates", label: "مواعيد السداد" },
            { value: "clints_comments_delete", label: "حذف متابعة" },
            { value: "clints_callsRecords", label: "سجلات المكالمات" },
            {
              value: "clints_callsRecords_download",
              label: "تحميل تسجيل المكالمات",
            },
            {
              value: "clints_callsRecords_play",
              label: "تشغيل تسجيل المكالمات",
            },
            { value: "clints_contacts_delete", label: "حذف جهة اتصال" },
            {
              value: "clints_contacts_default",
              label: "تعيين جهة اتصال رئيسية",
            },
            { value: "clints_lock", label: "قفل العميل" },
            { value: "clints_lock_history", label: "تاريخ القفل والفتح" },
            { value: "clints_documents", label: "مستندات العميل" },
            { value: "clints_documents_add", label: "اضافة مستند" },
            { value: "clints_documents_delete", label: "حذف مستند" },
          ],
        },
        {
          value: "client_query_",
          label: "استعلام العملاء",
          children: [{ value: "client_query", label: "عرض" }],
        },
        {
          value: "clints_archive_",
          label: "ارشيف العملاء",

          children: [
            { value: "clints_archive", label: "عرض" },
            { value: "clints_archive_move", label: "نقل من الارشيف" },
          ],
        },

        {
          value: "branches_supervisors_",
          label: "الفروع والمشرفين",
          children: [
            {
              value: "branches_",
              label: "الفروع",
              children: [
                { value: "branches", label: "عرض" },
                { value: "branches_add", label: "اضافة" },
                { value: "branches_edit", label: "تعديل" },
                { value: "branches_delete", label: "حذف" },
              ],
            },
            {
              value: "supervisors_",
              label: "المشرفين",
              children: [
                { value: "supervisors", label: "عرض" },
                { value: "supervisors_add", label: "اضافة" },
                { value: "supervisors_edit", label: "تعديل" },
                { value: "supervisors_delete", label: "حذف" },
              ],
            },
          ],
        },
        {
          value: "accounts",
          label: "الحسابات",
          children: [
            {
              value: "accounts_payments_",
              label: "السدادات",
              children: [
                { value: "accounts_payments", label: "عرض" },
                { value: "accounts_payments_edit", label: "تعديل" },
                { value: "accounts_payments_delete", label: "حذف" },
              ],
            },
          ],
        },
        {
          value: "collections_",
          label: "التحصيلات",
          children: [
            { value: "collections", label: "عرض" },
            { value: "collections_currentUser", label: "تحصيلاتي" },
            { value: "collections_users", label: "تحصيلات المستخدمين" },
          ],
        },
        {
          value: "roles_",
          label: "النظام الامني",
          children: [
            { value: "roles", label: "عرض" },
            { value: "roles_add", label: "اضافة" },
            { value: "roles_edit", label: "تعديل" },
            { value: "roles_delete", label: "حذف" },
          ],
        },

        {
          value: "Settings",
          label: "الاعدادات",
          children: [
            {
              value: "settings_comments",
              label: "اعدادت المتابعات",
              children: [
                {
                  value: "settings_comments_status_",
                  label: "حالات المتابعات",
                  children: [
                    { value: "settings_comments_status", label: "عرض" },
                    { value: "settings_comments_status_add", label: "اضافة" },
                    { value: "settings_comments_status_edit", label: "تعديل" },
                    { value: "settings_comments_status_delete", label: "حذف" },
                  ],
                },
              ],
            },

            {
              value: "settings_general",
              label: "اعدادات عامة",
              children: [
                {
                  value: "settings_general_archiveReasons_",
                  label: "اسباب الارشيف",
                  children: [
                    { value: "settings_general_archiveReasons", label: "عرض" },
                    {
                      value: "settings_general_archiveReasons_add",
                      label: "اضافة",
                    },
                    {
                      value: "settings_general_archiveReasons_edit",
                      label: "تعديل",
                    },
                    {
                      value: "settings_general_archiveReasons_delete",
                      label: "حذف",
                    },
                  ],
                },
                {
                  value: "settings_general_delinquencyReasons_",
                  label: "اسباب التعثر",
                  children: [
                    {
                      value: "settings_general_delinquencyReasons",
                      label: "عرض",
                    },
                    {
                      value: "settings_general_delinquencyReasons_add",
                      label: "اضافة",
                    },
                    {
                      value: "settings_general_delinquencyReasons_edit",
                      label: "تعديل",
                    },
                    {
                      value: "settings_general_delinquencyReasons_delete",
                      label: "حذف",
                    },
                  ],
                },
                {
                  value: "settings_general_collectionMethods_",
                  label: "طرق التحصيل",
                  children: [
                    {
                      value: "settings_general_collectionMethods",
                      label: "عرض",
                    },
                    {
                      value: "settings_general_collectionMethods_add",
                      label: "اضافة",
                    },
                    {
                      value: "settings_general_collectionMethods_edit",
                      label: "تعديل",
                    },
                    {
                      value: "settings_general_collectionMethods_delete",
                      label: "حذف",
                    },
                  ],
                },

                {
                  value: "settings_general_nationalities_",
                  label: "الجنسيات",
                  children: [
                    {
                      value: "settings_general_nationalities",
                      label: "عرض",
                    },
                    {
                      value: "settings_general_nationalities_add",
                      label: "اضافة",
                    },
                    {
                      value: "settings_general_nationalities_edit",
                      label: "تعديل",
                    },
                    {
                      value: "settings_general_nationalities_delete",
                      label: "حذف",
                    },
                  ],
                },
                {
                  value: "settings_general_updateSide_",
                  label: "جهات التحديث",
                  children: [
                    {
                      value: "settings_general_updateSide",
                      label: "عرض",
                    },
                    {
                      value: "settings_general_updateSide_add",
                      label: "اضافة",
                    },
                    {
                      value: "settings_general_updateSide_edit",
                      label: "تعديل",
                    },
                    {
                      value: "settings_general_updateSide_delete",
                      label: "حذف",
                    },
                  ],
                },
              ],
            },
          ],
        },

        {
          value: "reports_",
          label: "التقارير",
          children: [
            {
              value: "reports",
              label: "عرض",
            },

            {
              value: "reports_users_",
              label: "تقاريرالمستخدمين",
              children: [
                { value: "reports_users", label: "عرض" },
                { value: "report_users_comments", label: "تقريرالمتابعات" },
                {
                  value: "report_users_paymentDates",
                  label: "تقرير مواعيد السداد",
                },
              ],
            },
            {
              value: "reports_clints_",
              label: "تقارير العملاء",
              children: [
                { value: "reports_clints", label: "عرض" },
                {
                  value: "report_clintes_active",
                  label: "تقرير العملاء النشطين",
                },
                {
                  value: "report_clintes_inactive",
                  label: "تقرير العملاء الغير نشطين",
                },
              ],
            },
            {
              value: "report_portfolios_",
              label: "تقارير المحافظ",
              children: [{ value: "report_portfolios", label: "عرض" }],
            },

            {
              value: "report_quality_neglect_",
              label: "تقارير الجودة والاهمال",
              children: [{ value: "report_quality_neglect", label: "عرض" }],
            },
            {
              value: "report_collectors_",
              label: "تقارير المحصلين",
              children: [
                { value: "report_collectors", label: "عرض" },
                {
                  value: "report_collectors_currentUser_paymentDates",
                  label: "مواعيد السداد",
                },
                {
                  value: "report_collectors_currentUser_nextCalls",
                  label: "الاتصالات القادمة",
                },
                {
                  value: "report_collectors_currentUser_comments",
                  label: "متابعاتي",
                },
              ],
            },
          ],
        },
        phase1Node,
      ],
    },
  ];

  const [checked, setChecked] = useState<string[]>([]);
  const [expanded, setExpanded] = useState<string[]>([]);

  useEffect(
    () => {
      /* PortfolioForm normalises `permission` to the checked-key array. */
      setChecked(
        Array.isArray(values.permission)
          ? values.permission
          : values.permission_arr || []
      );
      setExpanded([
        "all",
        "calling_",
        "phase1_",
        ...PHASE1_PERMISSION_GROUPS.map((g) => `phase1_${g}_`),
      ]);
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  return (
    <AdaptableCard className="mb-4">
      <FormItem
        label={t("permission.name")}
        invalid={(errors.name && touched.name) as boolean}
        errorMessage={errors.name}
      >
        <Field
          type="text"
          autoComplete="new-password"
          name="name"
          placeholder={t("permission.name")}
          component={Input}
        />
      </FormItem>
      <FormItem
        label={t("permission.title")}
        invalid={(errors.permission && touched.permission) as boolean}
        errorMessage={errors.permission}
      >
        <Field name="permission">
          {({ field, form }: FieldProps) => (
            <CheckboxTree
              field={field}
              form={form}
              nodes={nodes}
              checked={checked}
              expanded={expanded}
              onCheck={(checked) => {
                setChecked(checked);
                form.setFieldValue(field.name, checked);
              }}
              onExpand={(expanded) => setExpanded(expanded)}
              direction={i18n.dir()}
              /* The package stylesheet is not loaded, so without explicit
                 icons a checked and an unchecked box looked identical. */
              icons={{
                check: <FaRegSquareCheck className="text-navy-600 dark:text-azure-300" />,
                uncheck: <FaRegSquare className="text-gray-500" />,
                halfCheck: <FaRegSquareCheck className="text-gray-500" />,
                expandClose: <MdOutlineFolder className="text-gray-500" />,
                expandOpen: <FaRegFolderOpen className="text-gray-500" />,
                expandAll: null,
                collapseAll: null,
                parentClose: null,
                parentOpen: null,
                leaf: null,
              }}
            />
          )}
        </Field>
      </FormItem>
      {/* 
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
      </FormItem> */}
    </AdaptableCard>
  );
};

export default Fields;
