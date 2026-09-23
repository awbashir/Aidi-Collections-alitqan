import { forwardRef, useState } from "react";
import { FormContainer } from "@/components/ui/Form";
import Button from "@/components/ui/Button";
import hooks from "@/components/ui/hooks";
import Fields from "./Fields";
import StickyFooter from "@/components/shared/StickyFooter";
import ConfirmDialog from "@/components/shared/ConfirmDialog";
import { Form, Formik, FormikProps } from "formik";
import cloneDeep from "lodash/cloneDeep";
import { HiOutlineTrash } from "react-icons/hi";
import { AiOutlineSave } from "react-icons/ai";
import * as Yup from "yup";
import { useTranslation } from "react-i18next";
import { t } from "i18next";

// eslint-disable-next-line  @typescript-eslint/no-explicit-any
type FormikRef = FormikProps<any>;

type InitialData = {
  id: string;
  name: string;
  monthly_target_percent?: number;
  monthly_target_is_percentage: boolean;
  monthly_target_amount?: number;
  is_active: boolean;
};

export type FormModel = Omit<InitialData, "tags"> & {
  tags: { label: string; value: string }[] | string[];
};

export type SetSubmitting = (isSubmitting: boolean) => void;

export type OnDeleteCallback = React.Dispatch<React.SetStateAction<boolean>>;

type OnDelete = (callback: OnDeleteCallback) => void;

type ContractForm = {
  initialData?: InitialData;
  type: "edit" | "new";
  onDiscard?: () => void;
  onDelete?: OnDelete;
  onFormSubmit: (formData: FormModel, setSubmitting: SetSubmitting) => void;
};

const { useUniqueId } = hooks;

const validationSchema = Yup.object().shape({
  name: Yup.string().required("Name is required"),
});

const DeleteProductButton = ({ onDelete }: { onDelete: OnDelete }) => {
  const [dialogOpen, setDialogOpen] = useState(false);

  const onConfirmDialogOpen = () => {
    setDialogOpen(true);
  };

  const onConfirmDialogClose = () => {
    setDialogOpen(false);
  };

  const handleConfirm = () => {
    onDelete?.(setDialogOpen);
  };

  return (
    <>
      <Button
        className="text-red-600"
        variant="plain"
        size="sm"
        icon={<HiOutlineTrash />}
        type="button"
        onClick={onConfirmDialogOpen}
      >
        {t("delete")}
      </Button>
      <ConfirmDialog
        isOpen={dialogOpen}
        type="danger"
        title={t("contracts.delete")}
        confirmText={t("confirm") as string}
        cancelText={t("cancel") as string}
        confirmButtonColor="red-600"
        onClose={onConfirmDialogClose}
        onRequestClose={onConfirmDialogClose}
        onCancel={onConfirmDialogClose}
        onConfirm={handleConfirm}
      >
        <p>{t("contracts.deleteConfirm")}</p>
      </ConfirmDialog>
    </>
  );
};

const ContractForm = forwardRef<FormikRef, ContractForm>((props, ref) => {
  const {
    nationalities,
    branches,
    roles,
    type,
    initialData = {
      id: "",
      name: "",
      is_active: true,
      monthly_target_is_percentage: false,
      monthly_target_percent: undefined,
      monthly_target_amount: undefined,

    },
    onFormSubmit,
    onDiscard,
    onDelete,
  } = props;

  const { t } = useTranslation();

  return (
    <>
      <Formik
        innerRef={ref}
        initialValues={{
          ...initialData,
        }}
        validationSchema={validationSchema}
        onSubmit={(values: FormModel, { setSubmitting }) => {
          const formData = cloneDeep(values);

          onFormSubmit?.(formData, setSubmitting);
        }}
      >
        {({ values, touched, errors, isSubmitting }) => (
          <Form>
            <FormContainer>
              <div className="grid grid-cols gap-4">
                <div>
                  <Fields touched={touched} errors={errors} values={values} />
                </div>
              </div>

              <StickyFooter
                className="-mx-8 px-8 flex items-center justify-between py-4"
                stickyClass="border-t border-gray-300 bg-white dark:border-night-border dark:bg-night-card"
              >
                <div>
                  {type === "edit" && (
                    <DeleteProductButton onDelete={onDelete as OnDelete} />
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

ContractForm.displayName = "ContractForm";

export default ContractForm;
