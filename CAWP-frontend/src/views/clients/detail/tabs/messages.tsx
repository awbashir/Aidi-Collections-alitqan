import Segment from "@/components/ui/Segment";
import Button from "@/components/ui/Button";
import { FormContainer } from "@/components/ui/Form";
import Notification from "@/components/ui/Notification";
import toast from "@/components/ui/toast";
import FormDesription from "./FormDesription";
import FormRow from "./FormRow";
import { Field, Form, Formik } from "formik";
import isLastChild from "@/utils/isLastChild";
import type {
  FieldProps,
  FormikTouched,
  FormikErrors,
  FieldInputProps,
  FormikProps,
} from "formik";
import * as Yup from "yup";
import { useTranslation } from "react-i18next";
import Input from "@/components/ui/Input";
import Select from "@/components/ui/Select";
import {
  apiGetCollectionTypes,
  apiGetCollectionMethods,
} from "@/services/ClientsService";
import { useState, useEffect } from "react";
import { useAppSelector, useAppDispatch, postPaymentDate } from "../store";
import { init, use } from "i18next";
import Card from "@/components/ui/Card";
import { HiCheckCircle } from "react-icons/hi";
import ReactDOM from "react-dom";

const Comments = ({
  data = {
    news: [],
    accountActivity: [],
    signIn: [],
    reminders: [],
    mentioned: [],
    replies: [],
    taskUpdate: [],
    assigned: [],
    newProduct: [],
    newOrder: [],
  },
}: NotificationSettingProps) => {
  const dispatch = useAppDispatch();
  const { t } = useTranslation();
  const [selectedContact, setSelectedContact] = useState(null);

  /*
  const sendMessage = async () => {
    try {
      const res = await fetch(
        "https://graph.facebook.com/v22.0/600914206435680/messages",
        {
          method: "POST",
          headers: {
            Authorization:
              "Bearer EAATsrYg3o2QBO4t0Pi5lw1sKO7gDODNMa4GSRKZCJh0CxfrD30ZA1xJZC0dlIr2XZBX9CwJCkLciu0ZA8NJoApm6Q81833yrdQIJA2OzKs9ZCopZCtZCTG38w5oQu1NwemIko61R1Fnr0VVURwRpFQREYE4wsl4Pjf5Jkwc6kTw1s2KGGhU4p8c83wF4ZCDepCRbZAf1h6K7sYjsawwffNpzdKxVBw",
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            messaging_product: "whatsapp",
            to: "966553377063",
            type: "template",
            template: {
              name: "hello_world",
              language: {
                code: "en_US",
              },
            },
          }),
        }
      );

      const data = await res.json();
      setResponse(JSON.stringify(data, null, 2));
    } catch (error) {
      console.error("Error sending message:", error);
      setResponse("Error sending message");
    }
  };
  */

  const extractTextFromJSX = (jsx) => {
    const div = document.createElement("div");
    ReactDOM.render(jsx, div);
    return div.innerHTML
      .replace(/<br\s*\/?>/gi, "\n")
      .replace(/<strong>(.*?)<\/strong>/gi, "*$1*")
      .replace(/<[^>]+>/g, "");
  };

  const handleContactChange = (selectedOption) => {
    console.log(selectedOption);
    setSelectedContact(selectedOption);
  };

  const sendMessage = () => {
    if (true) {
      const message = encodeURIComponent(extractTextFromJSX(messageContent));
      //electedContact?.label get last 9 digits and +966 in frist
      const contact = selectedContact?.label
        .slice(-9)
        .replace(/[^0-9]/g, "")
        .replace(/(\d{3})(\d{3})(\d{3})(\ d{3})/, "+966$1$2$3$4");

      const whatsappUrl = `https://wa.me/${contact}?text=${message}`;
      window.open(whatsappUrl, "_blank");
    } else {
      alert("Please select a contact first.");
    }
  };
  const clint = useAppSelector(
    (state) => state.crmCustomerDetails?.data?.clientData
  );

  const clintContacts = useAppSelector(
    (state) => state.crmCustomerDetails?.data?.ContactData
  );

  const userId = useAppSelector((state) => state.auth.user.id);

  const messageContent = (
    <div>
      عزيزنا العميل
      <br />
      السيد / <strong>{clint?.name}</strong>
      <br />
      <br />
      نود تذكيرك بضرورة سداد المديونيات المتعثرة لدى موبايلي على هويتك /
      <strong>
        {clint?.identityNumber
          ? clint.identityNumber.slice(-4) + "****************"
          : ""}
      </strong>
      <br />
      علما بأنه في حال التأخر في السداد فإنه سيتم إحالة المديونية للجهات
      القضائية المختصة .
      <br />
      تفاصيل المديونيات :
      <br />
      <br />
      رقم الخدمة متغير : <strong>{clint?.attribute?.MSISDN}</strong>
      <br />
      مبلغ المديونية : <strong>{clint?.amount}</strong> ريال
      <br /> رقم حساب سداد : <strong>{clint?.accountNumber}</strong>
      <br />
      <br /> يمكنك السداد عن طريق الموقع الرسمي لموبايلي أو زيارة أحد فروع
      موبايلي.
      <br />
      كما يمكنك السداد بنكيا عبر خدمة سداد باستخدام رقم الحساب كرقم مرجع سداد .
      <br />
      <br />
      برنامج التحصيل الذكي - وكيل موبايلي
    </div>
  );

  const cardFooter = (
    <div className="flex justify-end">
      <Button
        size="sm"
        variant="solid"
        disabled={!selectedContact || clint?.is_archive}
        onClick={sendMessage}
      >
        {t("send")}
      </Button>
    </div>
  );

  const headerExtraContent = (
    <span className="flex items-center">
      <span className="ms-3 font-extrabold">
        <Select
          size="sm"
          className="mb-4"
          options={clintContacts?.map((contact) => ({
            value: contact.id,
            label: contact.contact,
          }))}
          placeholder="اختر رقم"
          onChange={handleContactChange}
        ></Select>
      </span>
      <span className="ms-1 font-extrabold"> {t("status")}</span>
      <span className="text-emerald-500 text-xl">
        <HiCheckCircle />
      </span>
    </span>
  );

  return (
    <>
      <div>
        <div>
          <Card
            header={t("message_format")}
            headerExtra={headerExtraContent}
            footer={cardFooter}
          >
            <p>{messageContent}</p>
          </Card>
        </div>
      </div>
    </>
  );
};

export default Comments;
