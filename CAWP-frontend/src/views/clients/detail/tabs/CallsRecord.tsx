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
import TimeLineComment from "../components/Timeline";
import CallsRecordClient from "../components/CallsRecordClientTable";

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

  const clint = useAppSelector(
    (state) => state.crmCustomerDetails?.data?.clientData
  );

  const userId = useAppSelector((state) => state.auth.user.id);

  return (
    <>
      <CallsRecordClient />
    </>
  );
};

export default Comments;
