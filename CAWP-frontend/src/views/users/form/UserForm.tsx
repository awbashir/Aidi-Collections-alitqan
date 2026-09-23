import { forwardRef, useState } from "react";
import { FormContainer } from "@/components/ui/Form";
import Button from "@/components/ui/Button";
import hooks from "@/components/ui/hooks";
import UserFields from "./UserFields";
import UserFields2 from "./UserFields2";
import UserFields3 from "./UserFields3";
import StickyFooter from "@/components/shared/StickyFooter";
import ConfirmDialog from "@/components/shared/ConfirmDialog";
import { Form, Formik, FormikProps } from "formik";
import cloneDeep from "lodash/cloneDeep";
import { HiOutlineTrash } from "react-icons/hi";
import { AiOutlineSave } from "react-icons/ai";
import * as Yup from "yup";
import { useTranslation } from "react-i18next";
import { useAppSelector } from "../store"; // ← جديد — عدّل المسار حسب مكان store عندك لو مختلف

// eslint-disable-next-line  @typescript-eslint/no-explicit-any
type FormikRef = FormikProps<any>;

type InitialData = {
  id: string;
  frist_name: string;
  last_name: string;
  email: string;
  identityNumber: string;
  mobile: string;
  nationality_id: string;
  user_name: string;
  password: string;
  role_id: string;
  contract_id: string;
  target_is_percentage: boolean;
  target_amount: string;
  target_percent: string;

  branch_id: string;

  is_active: boolean;
};

export type FormModel = Omit<InitialData, "tags"> & {
  tags: { label: string; value: string }[] | string[];
};

export type SetSubmitting = (isSubmitting: boolean) => void;

export type OnDeleteCallback = React.Dispatch<React.SetStateAction<boolean>>;

type OnDelete = (callback: OnDeleteCallback) => void;

type ProductForm = {
  initialData?: InitialData;
  type: "edit" | "new";
  onDiscard?: () => void;
  onDelete?: OnDelete;
  onFormSubmit: (formData: FormModel, setSubmitting: SetSubmitting) => void;
};

const { useUniqueId } = hooks;

const validationSchema = Yup.object().shape({
  frist_name: Yup.string().required("First name is required"),
  last_name: Yup.string().required("Last name is required"),
  email: Yup.string().email("Invalid email").required("Email is required"),
  mobile: Yup.string().required("Mobile is required"),
  identityNumber: Yup.string().required("Identity number is required"),
  branch_id: Yup.string().required("Branch is required"),
  nationality_id: Yup.string().required("Nashionality is required"),
  role_id: Yup.string().required("Role is required"),
  user_name: Yup.string().required("User name is required"),
  password: Yup.string().required("Password is required"),
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
        Delete
      </Button>
      <ConfirmDialog
        isOpen={dialogOpen}
        type="danger"
        title="Delete product"
        confirmButtonColor="red-600"
        onClose={onConfirmDialogClose}
        onRequestClose={onConfirmDialogClose}
        onCancel={onConfirmDialogClose}
        onConfirm={handleConfirm}
      >
        <p>
          Are you sure you want to delete this product? All record related to
          this product will be deleted as well. This action cannot be undone.
        </p>
      </ConfirmDialog>
    </>
  );
};

const ProductForm = forwardRef<FormikRef, ProductForm>((props, ref) => {
  const {
    nationalities,
    contracts,
    branches,
    roles,
    type,
    initialData = {
      id: "",
      frist_name: "",
      last_name: "",
      email: "",
      identityNumber: "",
      mobile: "",
      nationality_id: "",
      password: "",
      role_id: "",
      user_name: "",
      branch_id: "",
      target_is_percentage: false,
      target_amount: "",
      target_percent: "",
      is_active: false,
      contract_id: null,
    },
    onFormSubmit,
    onDiscard,
    onDelete,
  } = props;

  const { t } = useTranslation();
  const { permissions } = useAppSelector((state) => state.auth.user); // ← جديد

  const validationSchema = Yup.object().shape({
    frist_name: Yup.string().required(t("requiredField")),
    last_name: Yup.string().required(t("requiredField")),
    email: Yup.string().email("Invalid email").required(t("requiredField")),
    mobile: Yup.string().required(t("requiredField")),
    identityNumber: Yup.string().required(t("requiredField")),
    branch_id: Yup.string().required(t("requiredField")),
    nationality_id: Yup.string().required(t("requiredField")),
    role_id: Yup.string().required(t("requiredField")),
    user_name: Yup.string().required(t("requiredField")),
    // The API no longer returns the password hash, so on edit an empty field means
    // "keep the current password" (the API only changes it when a value is sent).
    password:
      type === "new"
        ? Yup.string().required(t("requiredField"))
        : Yup.string().nullable(),
  });

  return (
    <>
      <Formik
        innerRef={ref}
        initialValues={{
          ...initialData,
          password: initialData.password ?? "",
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
              <div className="grid grid-cols-2 gap-4">
                {permissions.users_edit_general && (
                  <>
                    <div>
                      <UserFields2
                        touched={touched}
                        errors={errors}
                        values={values}
                        nationalities={nationalities}
                      />
                    </div>

                    <div>
                      <UserFields
                        touched={touched}
                        errors={errors}
                        values={values}
                        nationalities={nationalities}
                        roles={roles}
                        branches={branches}
                      />
                    </div>
                  </>
                )}

                {permissions.users_edit_target && (
                  <div>
                    <UserFields3
                      touched={touched}
                      errors={errors}
                      values={values}
                      contracts={contracts}
                    />
                  </div>
                )}
              </div>

              <StickyFooter
                className="-mx-8 px-8 flex items-center justify-between py-4"
                stickyClass="border-t border-gray-300 bg-white dark:border-night-border dark:bg-night-card"
              >
                {(permissions.users_edit_general ||
                  permissions.users_edit_target) && (
                  <div className="ms-auto flex flex-wrap items-center gap-2">
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
                )}
              </StickyFooter>
            </FormContainer>
          </Form>
        )}
      </Formik>
    </>
  );
});

ProductForm.displayName = "ProductForm";

export default ProductForm;
