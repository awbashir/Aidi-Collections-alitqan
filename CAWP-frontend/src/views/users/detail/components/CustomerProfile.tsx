import { useState } from "react";
import Card from "@/components/ui/Card";
import Avatar from "@/components/ui/Avatar";
import Button from "@/components/ui/Button";
import Notification from "@/components/ui/Notification";
import toast from "@/components/ui/toast";
import ConfirmDialog from "@/components/shared/ConfirmDialog";
import {
  FaFacebookF,
  FaTwitter,
  FaLinkedinIn,
  FaPinterestP,
} from "react-icons/fa";
import {
  HiPencilAlt,
  HiOutlineTrash,
  HiOutlineUser,
  HiOutlineCheck,
  HiOutlineX,
} from "react-icons/hi";
import { useNavigate } from "react-router-dom";
import {
  deleteCustomer,
  openEditCustomerDetailDialog,
  useAppDispatch,
  activateUser,
  deactivateUser,
  User,
} from "../store";
import EditCustomerProfile from "./EditCustomerProfile";
import { useTranslation } from "react-i18next";
import Tooltip from "@/components/ui/Tooltip";
import { usePermissions } from "@/utils/v2/hooks"; // Phase 3: protected employee fields
import { useAppSelector as useRootSelector } from "@/store";

type CustomerInfoFieldProps = {
  title?: string;
  value?: string | null;
  // Phase 3: a protected employee field; null means "hidden" unless the viewer may see it
  // (then null is just empty)
  hiddenIfNull?: boolean;
};

type CustomerProfileProps = {
  data?: Partial<User>;
};

const CustomerInfoField = ({ title, value, hiddenIfNull }: CustomerInfoFieldProps) => {
  const { t } = useTranslation();
  return (
    <div>
      <span>{title}</span>
      {value === null && hiddenIfNull ? (
        <p
          className="text-gray-500 dark:text-night-muted"
          title={String(t("p3.sessions.hiddenFieldTitle"))}
          data-testid="p3-hidden-field"
        >
          {t("p3.sessions.hiddenField")}
        </p>
      ) : (
        <p className="text-gray-700 dark:text-gray-200 font-semibold">{value}</p>
      )}
    </div>
  );
};

const CustomerProfileAction = (data) => {
  const dispatch = useAppDispatch();
  const [dialogOpen, setDialogOpen] = useState(false);
  const { t } = useTranslation();

  const navigate = useNavigate();

  const onDialogClose = () => {
    setDialogOpen(false);
  };

  const onDialogOpen = () => {
    setDialogOpen(true);
  };

  const onDelete = () => {
    setDialogOpen(false);
    if (id) {
      dispatch(deleteCustomer({ id }));
    }
    navigate("/app/crm/customers");
    toast.push(
      <Notification title={"Successfuly Deleted"} type="success">
        Customer successfuly deleted
      </Notification>
    );
  };

  const onEdit = () => {
    dispatch(openEditCustomerDetailDialog());
  };

  return (
    <>
      {/* Phase 3: the legacy activate/deactivate button is replaced on this page by the v2 action in
          UserSessionsCard (views/phase3/sessions, rendered by CustomerDetail): same keys
          (users_delete | users_details), plus a required reason and the audit. The users list
          switch (UsersTable.tsx) is unchanged. */}
      <Button block icon={<HiPencilAlt />} variant="solid" onClick={onEdit}>
        {t("edit")}
      </Button>

      <EditCustomerProfile />
    </>
  );
};

const popNotification = (keyword: string, navigate: Function, t: Function) => {
  toast.push(
    <Notification
      title={
        keyword === "deactivate" ? t("users.deactivate") : t("users.activate")
      }
      type="success"
      duration={2500}
    >
      {keyword === "deactivate"
        ? t("users.deactivateSuccess")
        : t("users.activateSuccess")}
    </Notification>,
    {
      placement: "top-center",
    }
  );
  navigate("/users");
};

// Phase 3: no longer rendered on the details page (see UserSessionsCard); kept unchanged.
// eslint-disable-next-line @typescript-eslint/no-unused-vars
const StatusUserButton = ({
  row,
  fetchData,
}: {
  row: User;
  fetchData: void;
}) => {
  const [dialogOpen, setDialogOpen] = useState(false);
  const { t } = useTranslation();
  const dispatch = useAppDispatch();
  const navigate = useNavigate();

  const onConfirmDialogOpen = () => {
    setDialogOpen(true);
  };

  const onConfirmDialogClose = () => {
    setDialogOpen(false);
  };

  const handleConfirm = async () => {
    const success = row.is_active
      ? await deactivateUser(row)
      : await activateUser(row);
    setDialogOpen(!success);
    if (success) {
      fetchData();
      popNotification(row.is_active ? "deactivate" : "activate", navigate, t);
    }
  };

  return (
    <>
      <span
        className={`cursor-pointer  ${
          row.is_active ? "text-red-500" : "text-green-500"
        }`}
        onClick={onConfirmDialogOpen}
      >
        {row.is_active ? (
          <Button block icon={<HiOutlineX />}>
            {t("deactivate")}
          </Button>
        ) : (
          <Button block icon={<HiOutlineCheck />}>
            {t("activate")}
          </Button>
        )}
      </span>

      <ConfirmDialog
        isOpen={dialogOpen}
        type="danger"
        title={row.is_active ? t("users.deactivate") : t("users.activate")}
        cancelText={t("cancel")}
        confirmText={t("confirm")}
        confirmButtonColor="red-600"
        onClose={onConfirmDialogClose}
        onRequestClose={onConfirmDialogClose}
        onCancel={onConfirmDialogClose}
        onConfirm={handleConfirm}
      >
        <p>
          {row.is_active
            ? t("users.confirmDeactivate")
            : t("users.confirmActivate")}
        </p>
      </ConfirmDialog>
    </>
  );
};

const CustomerProfile = ({ data = {} }: CustomerProfileProps) => {
  const { t } = useTranslation();
  // Phase 3: the API returns identityNumber / mobile / email as null to a viewer without
  // users_details / users_edit (own record excepted); show that as "Hidden", not as data
  const perms = usePermissions();
  const myId = useRootSelector((state) => state.auth.user.id);
  const mayNotSee = !(perms.users_details || perms.users_edit || (myId && myId === data.id));
  return (
    <Card>
      <div className="flex flex-col xl:justify-between h-full 2xl:min-w-[360px] mx-auto">
        <div className="flex xl:flex-col items-center gap-4">
          <Avatar
            size={90}
            shape="circle"
            src={data.img}
            icon={<HiOutlineUser />}
          />
          <h4 className="text-[20px] font-extrabold text-navy-600 dark:text-night-body">
            {data?.frist_name + " " + data?.last_name}
          </h4>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-1 gap-y-7 gap-x-4 mt-8">
          <CustomerInfoField title={t("email")} value={data.email} hiddenIfNull={mayNotSee} />
          <CustomerInfoField title={t("mobile")} value={data?.mobile} hiddenIfNull={mayNotSee} />
          <CustomerInfoField
            title={t("identity")}
            value={data.identityNumber}
            hiddenIfNull={mayNotSee}
          />
          <CustomerInfoField
            title={t("nationality")}
            value={data.nationality?.name}
          />
          <CustomerInfoField title={t("branch")} value={data.branch?.name} />

          <CustomerInfoField title={t("permissions")} value={data.role?.name} />
        </div>
        <div className="mt-4 flex flex-col xl:flex-row gap-2">
          <CustomerProfileAction data={data} />
        </div>
      </div>
    </Card>
  );
};

export default CustomerProfile;
