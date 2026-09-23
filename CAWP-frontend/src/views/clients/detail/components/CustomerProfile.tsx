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
import { HiPencilAlt, HiOutlineTrash, HiOutlineUser } from "react-icons/hi";
import { useNavigate } from "react-router-dom";
import {
  deleteCustomer,
  openEditCustomerDetailDialog,
  useAppDispatch,
  User,
} from "../store";
import EditCustomerProfile from "./EditCustomerProfile";
import { useTranslation } from "react-i18next";

type CustomerInfoFieldProps = {
  title?: string;
  value?: string;
};

type CustomerProfileProps = {
  data?: Partial<User>;
};

const CustomerInfoField = ({ title, value }: CustomerInfoFieldProps) => {
  return (
    <div>
      <span className="text-eyebrow">{title}</span>
      <p className="font-extrabold text-navy-600 dark:text-gray-200">{value}</p>
    </div>
  );
};

const CustomerProfileAction = ({ id }: { id?: string }) => {
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
      <Button block icon={<HiOutlineTrash />} onClick={onDialogOpen}>
        Delete
      </Button>
      <Button block icon={<HiPencilAlt />} variant="solid" onClick={onEdit}>
        Edit
      </Button>
      <ConfirmDialog
        isOpen={dialogOpen}
        type="danger"
        title="Delete customer"
        confirmButtonColor="red-600"
        onClose={onDialogClose}
        onRequestClose={onDialogClose}
        onCancel={onDialogClose}
        onConfirm={onDelete}
      >
        <p>
          Are you sure you want to delete this customer? All record related to
          this customer will be deleted as well. This action cannot be undone.
        </p>
      </ConfirmDialog>
      <EditCustomerProfile />
    </>
  );
};

const CustomerProfile = ({ data = {} }: CustomerProfileProps) => {
  const { t } = useTranslation();
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
          <h4 className="font-extrabold">{data?.frist_name + data?.last_name}</h4>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-1 gap-y-7 gap-x-4 mt-8">
          <CustomerInfoField title={t("email")} value={data.email} />
          <CustomerInfoField title={t("mobile")} value={data?.mobile} />
          <CustomerInfoField
            title={t("identity")}
            value={data.identityNumber}
          />
          <CustomerInfoField
            title={t("nationality")}
            value={data.nationality?.name}
          />
          <CustomerInfoField title={t("branch")} value={data.branch?.name} />

          <CustomerInfoField title={t("permissions")} value={data.role?.name} />
          <div className="mb-7">
            <span className="text-eyebrow">Social</span>
            <div className="flex mt-4">
              <Button
                className="me-2"
                shape="circle"
                size="sm"
                icon={<FaFacebookF className="text-gray-500" />}
              />
              <Button
                className="me-2"
                shape="circle"
                size="sm"
                icon={<FaTwitter className="text-gray-500" />}
              />
              <Button
                className="me-2"
                shape="circle"
                size="sm"
                icon={<FaLinkedinIn className="text-gray-500" />}
              />
              <Button
                className="me-2"
                shape="circle"
                size="sm"
                icon={<FaPinterestP className="text-gray-500" />}
              />
            </div>
          </div>
        </div>
        <div className="mt-4 flex flex-col xl:flex-row gap-2">
          <CustomerProfileAction id={data.id} />
        </div>
      </div>
    </Card>
  );
};

export default CustomerProfile;
