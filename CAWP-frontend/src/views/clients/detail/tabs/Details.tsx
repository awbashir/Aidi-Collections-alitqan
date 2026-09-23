import { useState } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { NumericFormat } from "react-number-format";

// UI Components
import Card from "@/components/ui/Card";
import Avatar from "@/components/ui/Avatar";
import IconText from "@/components/shared/IconText";
import Button from "@/components/ui/Button";
import Tooltip from "@/components/ui/Tooltip";
import StatusPill from "@/components/shared/StatusPill";

// Icons
import {
  HiMail,
  HiPhone,
  HiExternalLink,
  HiOutlineMail,
  HiOutlineTrash,
  HiOutlineBadgeCheck,
  HiBadgeCheck,
} from "react-icons/hi";
import { FaWhatsapp } from "react-icons/fa";
import { TbDeviceLandlinePhone } from "react-icons/tb";
import { LiaFaxSolid } from "react-icons/lia";

// Store and API
import {
  deleteClintContact,
  putClintContactUnsetDefaultByID,
  putClintContactDefaultByID,
  getContactsIdentityNumber,
  useAppDispatch,
  useAppSelector,
} from "../store";

// Custom Components
import ToastNotification from "@/components/shared/myComponents/ToastNotification";
import ConfirmDialog from "@/components/shared/myComponents/ConfirmDialog";

interface CustomerInfoProps {
  data: any;
  total_paments: number;
  clintContacts: any[];
}

const CustomerInfo = ({
  data,
  total_paments,
  clintContacts,
}: CustomerInfoProps) => {
  const { t } = useTranslation();
  const dispatch = useAppDispatch();

  const [isDialogOpen, setIsDialogOpen] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [contactToDelete, setContactToDelete] = useState<number | null>(null);

  // Helper function to get contact type icon
  const getContactTypeIcon = (contactType: string) => {
    const iconMap = {
      mobile: <HiPhone className="text-lg" />,
      phone: <TbDeviceLandlinePhone className="text-lg" />,
      email: <HiOutlineMail className="text-lg" />,
      fax: <LiaFaxSolid className="text-lg" />,
    };
    return iconMap[contactType] || <HiPhone className="text-lg" />;
  };

  // Dialog handlers
  const openConfirmDialog = (contactId: number) => {
    setContactToDelete(contactId);
    setIsDialogOpen(true);
  };

  const closeConfirmDialog = () => {
    setIsDialogOpen(false);
    setContactToDelete(null);
  };

  // Delete contact handler
  const handleDeleteContact = async () => {
    if (!contactToDelete) return;

    setIsLoading(true);

    try {
      const { status } = await deleteClintContact(contactToDelete);

      if (status) {
        dispatch(getContactsIdentityNumber(data?.identityNumber));
        ToastNotification(
          t("clients.deleteContactSuccess"),
          t("clients.deleteContact"),
          "success",
        );
      } else {
        ToastNotification(
          t("clients.deleteContactFailed"),
          t("clients.deleteContact"),
          "danger",
        );
      }
    } catch (error) {
      ToastNotification(
        t("clients.deleteContactFailed"),
        t("clients.deleteContact"),
        "danger",
      );
    } finally {
      setIsLoading(false);
      closeConfirmDialog();
    }
  };

  // Format currency display
  const formatCurrency = (amount: number) => (
    <span dir="ltr" className="font-extrabold tabular-nums text-navy-600">
      <NumericFormat
        displayType="text"
        value={amount || 0}
        thousandSeparator={true}
        fixedDecimalScale={true}
        decimalScale={2}
      />
    </span>
  );

  // Filter active contacts
  const activeContacts =
    clintContacts?.filter((contact) => !contact.is_deleted) || [];

  // Calculate remaining amount
  const remainingAmount = (data?.amount || 0) - (total_paments || 0);

  return (
    <div className="grid grid-row-flow grid-cols-2 gap-4">
      {/* General Information Card */}
      <div className="row-span-3">
        <Card>
          <div className="mb-4 flex items-center">
            <h5 className="mb-0">{t("genralInformation")}</h5>
            {data?.is_default && (
              <div className="ms-3">
                <StatusPill tone="neutral" size="md">
                  {t("archive.archived")}
                </StatusPill>
              </div>
            )}
          </div>

          <InfoSection
            title={t("clients.accountNumber")}
            content={
              <span
                dir="ltr"
                title={data?.accountNumber}
                className="block truncate tabular-nums"
              >
                {data?.accountNumber}
              </span>
            }
          />
          <InfoSection title={t("clients.name")} content={data?.name} />
          <InfoSection
            title={t("clients.identityNumber")}
            content={
              <span
                dir="ltr"
                title={data?.identityNumber}
                className="block truncate tabular-nums"
              >
                {data?.identityNumber}
              </span>
            }
          />
          <InfoSection
            title={t("amount")}
            content={formatCurrency(data?.amount)}
          />
          <InfoSection
            title={t("contracts.contract")}
            content={data?.contract?.name}
          />
          <InfoSection
            title={t("users.name")}
            content={data?.user?.name}
            isLast
          />
        </Card>
      </div>

      {/* Payment Details Card */}
      <div>
        <Card>
          <h5 className="mb-4">{t("payments.details")}</h5>
          <InfoSection
            title={t("amount")}
            content={formatCurrency(data?.amount)}
          />
          <InfoSection
            title={t("payments.paidUP")}
            content={formatCurrency(total_paments)}
          />
          <InfoSection
            title={t("remaining")}
            content={formatCurrency(remainingAmount)}
            isLast
          />
        </Card>
      </div>

      {/* Contact Details Card */}
      <div>
        <Card>
          <h5 className="mb-4">{t("contacts.details")}</h5>

          {activeContacts.length > 0 ? (
            activeContacts.map((contact, index) => (
              <ContactItem
                key={contact.id || index}
                contact={contact}
                data={data}
                onDelete={() => openConfirmDialog(contact.id)}
                getIcon={getContactTypeIcon}
                t={t}
              />
            ))
          ) : (
            <p className="text-[15px] text-gray-600">{t("contacts.noContacts")}</p>
          )}
        </Card>
      </div>

      {/* Extra Information Card */}
      <div className="col-span-2">
        <Card>
          <h5 className="mb-4">{t("extraInformation")}</h5>
          <div className="grid grid-row-flow grid-cols-2 gap-4">
            {data?.attribute &&
              Object.entries(data.attribute)
                .filter(([_, value]) => value)
                .map(([key, value]) => (
                  <ExtraInfoItem
                    key={key}
                    label={key}
                    value={value as string}
                  />
                ))}
          </div>
        </Card>
      </div>

      {/* Confirm Delete Dialog */}
      <ConfirmDialog
        isOpen={isDialogOpen}
        type="danger"
        title={t("clients.deleteContact")}
        cancelText={t("cancel")}
        confirmText={t("confirm")}
        confirmButtonColor="red-600"
        onClose={closeConfirmDialog}
        onRequestClose={closeConfirmDialog}
        onCancel={closeConfirmDialog}
        onConfirm={handleDeleteContact}
        loading={isLoading}
      >
        <p>{t("clients.confirmDeleteContact")}</p>
      </ConfirmDialog>
    </div>
  );
};

// Helper Components
const InfoSection = ({ title, content, isLast = false }) => (
  <>
    <div className="text-eyebrow mb-1">{title}</div>
    <address className="not-italic">
      <div className="text-[15px] text-gray-700">{content}</div>
    </address>
    {!isLast && <hr className="my-4" />}
  </>
);

const buildWhatsAppLink = (rawNumber: string): string | null => {
  const digits = (rawNumber || "").replace(/\D/g, "");
  if (digits.length < 9) return null;
  const last9 = digits.slice(-9);
  return `https://wa.me/966${last9}`;
};

const ContactItem = ({ contact, data, onDelete, getIcon, t }) => {
  const { permissions } = useAppSelector((state) => state.auth.user);
  const dispatch = useAppDispatch();
  const [isSettingDefault, setIsSettingDefault] = useState(false);

  const formatDate = (dateString: string) =>
    new Date(dateString).toISOString().split("T")[0];

  const whatsappLink = buildWhatsAppLink(contact.contact);
  const handleSetDefault = async () => {
    if (isSettingDefault) return;

    setIsSettingDefault(true);

    try {
      let result;

      if (contact.is_default) {
        // If contact is already default, unset it
        result = await putClintContactUnsetDefaultByID(contact.id);
      } else {
        // If contact is not default, set it as default
        result = await putClintContactDefaultByID(contact.id);
      }

      if (result.status) {
        // Refresh the contacts list
        dispatch(getContactsIdentityNumber(data?.identityNumber));

        const action = contact.is_default ? "unset" : "set";
        ToastNotification(
          t(`clients.${action}DefaultContactSuccess`),
          t("clients.setDefaultContact"),
          "success",
        );
      } else {
        ToastNotification(
          t("clients.setDefaultContactFailed"),
          t("clients.setDefaultContact"),
          "danger",
        );
      }
    } catch (error) {
      console.error("Error setting default contact:", error);
      ToastNotification(
        t("clients.setDefaultContactFailed"),
        t("clients.setDefaultContact"),
        "danger",
      );
    } finally {
      setIsSettingDefault(false);
    }
  };

  return (
    <div className="flex min-h-[44px] items-center justify-between gap-3 border-b border-gray-200 transition-colors duration-150 last:border-b-0 hover:bg-gray-50">
      <IconText
        className="min-w-0 text-gray-500"
        icon={getIcon(contact.contact_type.name)}
      >
        <span
          dir="ltr"
          title={contact.contact}
          className="truncate font-extrabold tabular-nums text-navy-600"
        >
          {contact.contact}
        </span>
        <span className="mx-1 text-gray-400"> - </span>
        <span className="text-[13.5px] tabular-nums text-gray-600">
          {formatDate(contact.create_at)}
        </span>
      </IconText>

      <div className="flex items-center gap-1">
        {whatsappLink && (
          <Tooltip title={t("openWhatsapp")}>
            <a
              href={whatsappLink}
              target="_blank"
              rel="noopener noreferrer"
              className="cursor-pointer text-green-600 p-2 hover:bg-green-50 rounded inline-block"
            >
              <FaWhatsapp />
            </a>
          </Tooltip>
        )}

        {permissions.clints_contacts_default && (
          <Tooltip
            title={
              contact.is_default
                ? t("clients.unsetDefaultContact")
                : t("clients.setDefaultContact")
            }
          >
            <span
              className={`cursor-pointer p-2 rounded transition-colors ${
                isSettingDefault
                  ? "text-gray-400 cursor-not-allowed"
                  : contact.is_default
                  ? "text-green-600 hover:bg-green-50"
                  : "text-gray-600 hover:bg-green-50"
              }`}
              onClick={handleSetDefault}
            >
              {contact.is_default ? <HiBadgeCheck /> : <HiOutlineBadgeCheck />}
            </span>
          </Tooltip>
        )}

        {permissions.clints_contacts_delete && (
          <Tooltip title={t("clients.deleteContact")}>
            <span
              className="cursor-pointer text-red-600 p-2 hover:bg-red-50 rounded"
              onClick={onDelete}
            >
              <HiOutlineTrash />
            </span>
          </Tooltip>
        )}
      </div>
    </div>
  );
};

const ExtraInfoItem = ({ label, value }) => (
  <div>
    <span className="text-eyebrow">{label}</span>
    <address className="not-italic">
      <div className="mt-1 text-[15px] text-gray-700">{value}</div>
    </address>
    <hr className="my-3" />
  </div>
);

export default CustomerInfo;
