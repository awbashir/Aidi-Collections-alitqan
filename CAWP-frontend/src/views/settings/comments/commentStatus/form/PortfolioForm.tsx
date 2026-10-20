import { forwardRef } from "react";
import { FormContainer } from "@/components/ui/Form";
import Button from "@/components/ui/Button";
import hooks from "@/components/ui/hooks";
import Fields, { DEFAULT_STATUS_COLOR } from "./Fields";
import DeleteStatusButton from "../list/components/DeleteStatusButton";
import StickyFooter from "@/components/shared/StickyFooter";
import { Form, Formik, FormikProps } from "formik";
import cloneDeep from "lodash/cloneDeep";
import { AiOutlineSave } from "react-icons/ai";
import * as Yup from "yup";
import { useTranslation } from "react-i18next";
import { t } from "i18next";

// eslint-disable-next-line  @typescript-eslint/no-explicit-any
type FormikRef = FormikProps<any>;

type InitialData = {
  id: string;
  name: string;
  is_active: boolean | string;
  /** null = a general status, usable on every contract (0068) */
  contract_id: string | null;
  CommentReachability_id: string | null;
  color: string;
  /** a sub-status's main status (0068) */
  parent_id?: string | null;
  /** 0079: a note with this status needs a promise (a sub-status carries its main status's flag) */
  requires_promise?: boolean;
};

export type FormModel = InitialData;

export type SetSubmitting = (isSubmitting: boolean) => void;

export type OnDeleteCallback = React.Dispatch<React.SetStateAction<boolean>>;

type OnDelete = (callback: OnDeleteCallback) => void;

type PortfolioForm = {
  initialData?: InitialData;
  type: "edit" | "new";
  contracts?: { id: string; name: string }[];
  reachability?: { id: string; name: string }[];
  onDiscard?: () => void;
  onDelete?: OnDelete;
  /** edit page: after the status was (soft) deleted */
  onDeleted?: () => void;
  onFormSubmit: (formData: FormModel, setSubmitting: SetSubmitting) => void;
};

const { useUniqueId } = hooks;

// 0068: the contract is optional (none = a general status) and so is the parent;
// the colour defaults to white
const validationSchema = Yup.object().shape({
  name: Yup.string().required(t("requiredField")),
  contract_id: Yup.string().nullable(),
  parent_id: Yup.string().nullable(),
  CommentReachability_id: Yup.string().nullable().required(t("requiredField")),
  is_active: Yup.string().required(t("requiredField")),
  color: Yup.string().nullable(),
});

const PortfolioForm = forwardRef<FormikRef, PortfolioForm>((props, ref) => {
  const {
    contracts,
    reachability,
    type,
    initialData = {
      id: "",
      name: "",
      is_active: "",
      contract_id: null,
      CommentReachability_id: null,
      color: DEFAULT_STATUS_COLOR,
      parent_id: null,
      requires_promise: false,
    },
    onFormSubmit,
    onDiscard,
    onDeleted,
  } = props;

  const { t } = useTranslation();

  return (
    <>
      <Formik
        innerRef={ref}
        initialValues={{
          ...initialData,
          parent_id: initialData.parent_id ?? null,
          color: initialData.color || DEFAULT_STATUS_COLOR,
          requires_promise: initialData.requires_promise === true,
        }}
        validationSchema={validationSchema}
        onSubmit={(values: FormModel, { setSubmitting }) => {
          const formData = cloneDeep(values);
          // empty selects are sent as null: no contract = general, no parent = main status
          formData.contract_id = formData.contract_id || null;
          formData.parent_id = formData.parent_id || null;
          formData.color = formData.color || DEFAULT_STATUS_COLOR;
          formData.requires_promise = formData.requires_promise === true;

          onFormSubmit?.(formData, setSubmitting);
        }}
      >
        {({ values, touched, errors, isSubmitting }) => (
          <Form>
            <FormContainer>
              <div className="grid grid-cols gap-4">
                <div>
                  <Fields
                    touched={touched}
                    errors={errors}
                    values={values}
                    contracts={contracts}
                    reachability={reachability}
                  />
                </div>
              </div>

              <StickyFooter
                className="-mx-8 px-8 flex items-center justify-between py-4"
                stickyClass="border-t border-gray-300 bg-white dark:border-night-border dark:bg-night-card"
              >
                <div>
                  {type === "edit" && initialData.id && (
                    <DeleteStatusButton
                      variant="button"
                      id={initialData.id}
                      name={initialData.name}
                      isMain={!initialData.parent_id}
                      onDeleted={() => onDeleted?.()}
                    />
                  )}
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <Button
                    size="sm"
                    type="button"
                    onClick={() => onDiscard?.()}
                  >
                    {t("discard")}
                  </Button>
                  <Button
                    size="sm"
                    variant="solid"
                    loading={isSubmitting}
                    icon={<AiOutlineSave />}
                    type="submit"
                  >
                    {t("save")}
                  </Button>
                </div>
              </StickyFooter>
            </FormContainer>
          </Form>
        )}
      </Formik>
    </>
  );
});

PortfolioForm.displayName = "PortfolioForm";

export default PortfolioForm;
