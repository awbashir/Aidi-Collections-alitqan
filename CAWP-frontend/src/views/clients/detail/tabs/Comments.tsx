import { useState, useEffect, useRef } from "react";
import { Field, Form, Formik } from "formik";
import * as Yup from "yup";
import { useTranslation } from "react-i18next";

// UI Components
import Button from "@/components/ui/Button";
import { FormContainer } from "@/components/ui/Form";
import Notification from "@/components/ui/Notification";
import toast from "@/components/ui/toast";
import Card from "@/components/ui/Card";
import Input from "@/components/ui/Input";
import Select from "@/components/ui/Select";

// Local Components
import FormDesription from "./FormDesription";
import FormRow from "./FormRow";
import TimeLineComment from "../components/Timeline";

// Services & Store
import {
  apiGetContactsMethods,
  apiGetCommentReachability,
  apiGetCommentStatus,
  apiGetCdr,
} from "@/services/ClientsService";
import { apiGetDelinquencyReasons } from "@/services/SettingsService";

import {
  useAppSelector,
  useAppDispatch,
  postComments,
  getComments,
  putClintsDelinquencyByID,
} from "../store";

import { noteStatusOptions, subStatusesOf } from "@/utils/commentStatusTree";
import { useCaseListNav } from "@/utils/v2/caseListNav";
import { callDock, useCallDock } from "@/views/phase5/live/callDock";
import type { DialedNumber } from "@/views/phase5/live/callDock";
import { factsFromCase } from "@/views/phase5/wrapup/caseFacts";
import {
  CallResultDone,
  CallResultForm,
} from "@/views/phase5/wrapup/CallResultForm";
import RequiredPromiseFields from "@/views/phase1/money/RequiredPromiseFields";
import {
  announcePromiseChanged,
  emptyRequiredPromise,
  requiredPromiseLegacyFields,
  requiredPromiseValid,
  statusRequiresPromise,
} from "@/views/phase1/money/requiredPromise";
import type { RequiredPromiseDraft } from "@/views/phase1/money/requiredPromise";
import { riyadhToday } from "@/utils/v2/format";

// Types
import type { FieldProps } from "formik";

interface NotificationSettingProps {
  data?: {
    news: any[];
    accountActivity: any[];
    signIn: any[];
    reminders: any[];
    mentioned: any[];
    replies: any[];
    taskUpdate: any[];
    assigned: any[];
    newProduct: any[];
    newOrder: any[];
  };
}

interface ProfileFormModel {
  comment_status_id: string;
  comment_sub_status_id?: string;
  contactMethod_id: string;
  contact: string;
  nextContactTime: string;
  nextContactDate: string;
  comment: string;
  comment_reachability_id: string;
  clint_id: string;
  user_id: string;
  call_id: string;
  contactNumber: string;
  next_contact_date?: string;
  delinquencyReason_id: string;
}

const Comments = ({ data }: NotificationSettingProps) => {
  const { t } = useTranslation();
  const dispatch = useAppDispatch();

  // Selectors
  const clint = useAppSelector(
    (state) => state.crmCustomerDetails?.data?.clientData
  );
  const userId = useAppSelector((state) => state.auth.user.id);
  const comments = useAppSelector(
    (state) => state.crmCustomerDetails?.data?.comments
  );
  const clintContacts = useAppSelector(
    (state) => state.crmCustomerDetails?.data?.ContactData
  );
  const clintContactsLoading = useAppSelector(
    (state) => state.crmCustomerDetails?.data?.ContactDataLoading
  );

  // State
  const [contactMethods, setContactMethods] = useState([]);
  const [contactMethodsLoading, setContactMethodsLoading] = useState(false);
  const [lastComment, setLastComment] = useState<any>(null);
  const [commentReachability, setCommentReachability] = useState([]);
  const [commentReachabilityLoading, setCommentReachabilityLoading] =
    useState(false);
  const [delinquencyReasonsLoading, setDelinquencyReasonsLoading] =
    useState(false);
  const [delinquencyReasonsList, setDelinquencyReasons] = useState([]);
  const [commentStatus, setCommentStatus] = useState([]);
  const [commentStatusLoading, setCommentStatusLoading] = useState(false);
  const [call, setCall] = useState([]);
  /* 0079: a promise-to-pay status («وعد بالسداد») needs the promise amount and
   * due date (or an open promise linked); POST /comments creates it with the note. */
  const [reqPromise, setReqPromise] = useState<RequiredPromiseDraft>(
    emptyRequiredPromise()
  );
  const [promiseOutstanding, setPromiseOutstanding] = useState<string | null>(
    null
  );
  const [promiseTried, setPromiseTried] = useState(false);

  /* 2026-10-17, one entry: while a call of this case waits for its wrap-up,
   * this form is the call's result ("نتيجة المكالمة"): one save closes the
   * call and writes the note (POST /v2/calls/<id>/wrapup). Then: call the
   * next number, go to the previous / next client, or write another note. */
  const dock = useCallDock();
  const caseKey = clint?.id ? String(clint.id) : null;
  const resultAttempt =
    caseKey && dock.resultFor?.caseId === caseKey
      ? dock.resultFor.attemptId
      : null;
  const [resultDone, setResultDone] = useState<DialedNumber | null>(null);
  const listNav = useCaseListNav(caseKey);
  /* The case as this page loaded it: contract (statuses), lock, archive. */
  const facts = factsFromCase(clint as never);
  const formTop = useRef<HTMLDivElement>(null);
  useEffect(() => setResultDone(null), [caseKey]);
  useEffect(() => {
    if (resultAttempt && dock.focus.tick)
      formTop.current?.scrollIntoView({ block: "start", behavior: "smooth" });
  }, [dock.focus.tick, resultAttempt]);
  /* The number just dialed on this case (the dock's live view) starts the form. */
  const dialed = callDock.lastDialed(caseKey);

  // Validation Schema
  const validationSchema = Yup.object().shape({
    comment_status_id: Yup.string().required(t("requiredField")),
    comment: Yup.string().required(t("requiredField")),
    contactMethod_id: Yup.string().required(t("requiredField")),
    call_id: Yup.string().test(
      "call-required",
      t("requiredField"),
      function (value) {
        // Phone call method ID
        if (
          this.parent.contactMethod_id ===
          "b55aa031-9349-4a3a-ad14-1f9d6821a342"
        ) {
          return !!value;
        }
        return true;
      }
    ),
  });

  // Constants
  const EMAIL_METHOD_ID = "25a02e31-9b4a-4940-9ef4-283f38d4b52d";
  const PHONE_METHOD_ID = "b55aa031-9349-4a3a-ad14-1f9d6821a342";
  const MOBILE_METHOD_ID = "ff081c7e-4500-4a75-ae36-580f2ae90834";

  const clintContactsOptions = clintContacts
    ?.filter((option: any) => option.contact?.trim())
    ?.map((option: any) => ({
      label: option.contact,
      value: option.id,
      type: option.contact_type?.name,
    }));

  // @ts-ignore - API response contains these properties
  const commentsStatusOptions = commentStatus?.map((option: any) => ({
    label: option.name,
    value: option.id,
    reachability_id: option.CommentReachability_id,
    contract_id: option.contract_id,
    parent_id: option.parent_id ?? null,
    deleted_at: option.deleted_at ?? null,
    requires_promise: option.requires_promise === true,
  }));

  // The shared note-status rule (utils/commentStatusTree.noteStatusOptions, the same as
  // the wrap-up's): the contract's main statuses + the general ones; a status without a
  // reachability fits every reachability. The last note's status is preselected only while
  // it is still one of them.
  const statusesFor = (reach: string | null | undefined) =>
    noteStatusOptions(commentsStatusOptions, facts.contractId, reach);
  const liveMainIds = new Set(
    statusesFor(lastComment?.comment_reachability_id).map((s: any) => s.value)
  );
  /* The number field: every number of the client (mobile, landline...), or
   * the e-mails for the e-mail method; the value sent is the chosen one. */
  const contactsFor = (method: string) =>
    (clintContactsOptions || []).filter((option: any) =>
      method === EMAIL_METHOD_ID ? option.type === "email" : option.type !== "email"
    );
  const last9 = (v?: string | null) => (v || "").replace(/\D/g, "").slice(-9);
  const dialedContact =
    dialed &&
    (clintContactsOptions || []).find(
      (option: any) =>
        option.type !== "email" &&
        (option.value === dialed.contactId ||
          (!!dialed.value && last9(option.label) === last9(dialed.value)))
    );

  // Form Initial Values
  const initialValues = {
    comment_status_id:
      lastComment?.comment_status_id &&
      liveMainIds.has(lastComment.comment_status_id)
        ? lastComment.comment_status_id
        : "",
    comment_sub_status_id: "",
    contactMethod_id: MOBILE_METHOD_ID,
    contact: dialedContact?.value || "",
    nextContactTime: "",
    nextContactDate: "",
    comment: "",
    comment_reachability_id: lastComment?.comment_reachability_id || "",
    clint_id: clint?.id || "",
    user_id: userId || "",
    call_id: "",
    contactNumber: "",
    delinquencyReason_id: clint?.delinquencyReason_id || "",
  };

  // API Calls
  const fetchContactsMethods = async () => {
    setContactMethodsLoading(true);
    try {
      const response = await apiGetContactsMethods();
      setContactMethods(response.data.contact_methods);
    } catch (error) {
      console.error("Error fetching contact methods:", error);
    } finally {
      setContactMethodsLoading(false);
    }
  };

  const fetchCommentReachability = async () => {
    setCommentReachabilityLoading(true);
    try {
      const response = await apiGetCommentReachability();
      setCommentReachability(response.data.comment_reachabilitys);
    } catch (error) {
      console.error("Error fetching comment reachability:", error);
    } finally {
      setCommentReachabilityLoading(false);
    }
  };

  const fetchDelinquencyReasons = async () => {
    setDelinquencyReasonsLoading(true);
    try {
      const response = await apiGetDelinquencyReasons();
      setDelinquencyReasons(response.data.delinquency_reasons);
    } catch (error) {
      console.error("Error fetching delinquency reasons:", error);
    } finally {
      setDelinquencyReasonsLoading(false);
    }
  };

  const fetchCommentStatus = async () => {
    setCommentStatusLoading(true);
    try {
      const response = await apiGetCommentStatus();
      setCommentStatus(response.data.comment_statuses);
    } catch (error) {
      console.error("Error fetching comment status:", error);
    } finally {
      setCommentStatusLoading(false);
    }
  };

  const fetchCdr = async () => {
    try {
      const response = await apiGetCdr();
      setCall(response.data);
    } catch (error) {
      console.error("Error fetching CDR:", error);
    }
  };

  // The PBX calls (CDR) to the chosen number, for the "phone call" method
  const callsToNumber = (phoneNumber?: string | null) => {
    if (!phoneNumber) return [];
    const last9Digits = phoneNumber.slice(-9);
    return call.filter(
      (callItem: any) =>
        callItem.dst?.slice(-9) === last9Digits && callItem.duration > 0
    );
  };

  const onFormSubmit = async (
    values: ProfileFormModel,
    setSubmitting: (isSubmitting: boolean) => void
  ) => {
    // 0079: the required promise is checked before anything is sent
    const needsPromise = statusRequiresPromise(
      commentsStatusOptions,
      values.comment_status_id,
      values.comment_sub_status_id
    );
    if (
      needsPromise &&
      !requiredPromiseValid(reqPromise, riyadhToday(), promiseOutstanding)
    ) {
      setPromiseTried(true);
      toast.push(
        <Notification title={String(t("p10.promise.fixFields"))} type="danger" />,
        { placement: "top-center" }
      );
      setSubmitting(false);
      return;
    }

    // Handle delinquency update separately (but don't return early)
    if (
      values.delinquencyReason_id &&
      values.delinquencyReason_id !== clint?.delinquencyReason_id
    ) {
      try {
        await dispatch(
          putClintsDelinquencyByID({
            clint_id: clint?.id,
            delinquencyReason_id: values.delinquencyReason_id,
          })
        );
      } catch (error) {
        console.error("Error updating delinquency:", error);
      }
    }

    console.log("is ccoming here");

    // Continue with posting the comment
    const sendValues = { ...values };

    // Combine date and time
    if (sendValues.nextContactDate) {
      if (sendValues.nextContactTime) {
        // The time is typed in Riyadh time; without the offset the API stored it as UTC
        // (10:30 came back as 13:30).
        sendValues.next_contact_date = `${sendValues.nextContactDate} ${sendValues.nextContactTime}+03:00`;
      } else {
        sendValues.next_contact_date = sendValues.nextContactDate;
      }
    }

    // The chosen number itself (not the select's text, which could be its placeholder)
    const chosen = (clintContactsOptions || []).find(
      (option: any) => option.value === values.contact
    );
    sendValues.contactNumber = chosen?.label || null;
    // No reachability chosen: none is sent (a blank id is refused by the database)
    if (!sendValues.comment_reachability_id)
      delete (sendValues as Partial<ProfileFormModel>).comment_reachability_id;

    // Clean up unnecessary fields
    delete sendValues.nextContactTime;
    delete sendValues.nextContactDate;
    delete sendValues.contact;
    delete sendValues.delinquencyReason_id;
    // 0068: the optional sub-status is sent only when one was chosen
    if (!sendValues.comment_sub_status_id) delete sendValues.comment_sub_status_id;
    // 0079: promise_amount + promise_date (or promise_id) with a promise-to-pay status
    const payload = needsPromise
      ? { ...sendValues, ...requiredPromiseLegacyFields(reqPromise) }
      : sendValues;

    // Post the comment
    dispatch(postComments(payload as unknown as Parameters<typeof postComments>[0]))
      .then((action: any) => {
        // a refused note comes back as 201 {status: false, message} (e.g. a deleted status)
        if (action?.payload?.status === false) {
          toast.push(
            <Notification
              title={action.payload.message || t("comments.addFail")}
              type="danger"
            />,
            { placement: "top-center" }
          );
          return;
        }
        dispatch(getComments(clint?.id));
        if (needsPromise) {
          announcePromiseChanged(clint?.id ? String(clint.id) : null);
          setReqPromise(emptyRequiredPromise());
          setPromiseTried(false);
        }
        toast.push(
          <Notification
            title={String(
              needsPromise
                ? t("p10.promise.noteSaved")
                : t("comments.addSuccess")
            )}
            type="success"
          />,
          { placement: "top-center" }
        );
      })
      .catch(() => {
        toast.push(
          <Notification title={t("comments.addFail")} type="danger" />,
          { placement: "top-center" }
        );
      })
      .finally(() => {
        setSubmitting(false);
      });
  };

  // Options for selects
  const contactMethodsOptions = contactMethods?.map((option: any) => ({
    label: option.name,
    value: option.id,
  }));

  const commentsReachabilityOptions = commentReachability?.map(
    (option: any) => ({
      label: option.name,
      value: option.id,
    })
  );

  const delinquencyReasonsOptions = delinquencyReasonsList?.map(
    (option: any) => ({
      label: option.name,
      value: option.id,
    })
  );

  const callOptionsFor = (phoneNumber?: string | null) =>
    callsToNumber(phoneNumber).map((option: any) => ({
      label: `${option.dst} - ${option.calldate} - المدة: ${option.duration}`,
      value: option.uniqueid,
    }));

  // Effects
  useEffect(() => {
    if (comments?.length) {
      const latest = comments.reduce((latest: any, comment: any) =>
        new Date(comment.create_at) > new Date(latest.create_at)
          ? comment
          : latest
      );
      setLastComment(latest);
    }
  }, [comments]);

  useEffect(() => {
    fetchContactsMethods();
    fetchCommentReachability();
    fetchDelinquencyReasons();
    fetchCommentStatus();
    fetchCdr();
  }, []);

  useEffect(() => {
    if (clint?.id) {
      dispatch(getComments(clint.id));
    }
  }, [clint?.id, dispatch]);

  return (
    <div className="flex flex-col gap-4">
      <div ref={formTop} className="scroll-mt-4" />
      {resultAttempt && caseKey ? (
        <CallResultForm
          key={resultAttempt}
          attemptId={resultAttempt}
          facts={facts}
          onSaved={(_res, d) => {
            setResultDone(d);
            callDock.resultDone(resultAttempt);
          }}
        />
      ) : resultDone && caseKey ? (
        <CallResultDone
          caseId={caseKey}
          facts={facts}
          dialed={resultDone}
          nav={listNav}
          onNewNote={() => setResultDone(null)}
        />
      ) : (
      <Card>
        <Formik
          enableReinitialize
          initialValues={initialValues}
          validationSchema={validationSchema}
          onSubmit={(values, { setSubmitting }) => {
            setSubmitting(true);
            setTimeout(() => onFormSubmit(values, setSubmitting), 1000);
          }}
        >
          {({ values, touched, errors, isSubmitting }) => {
            const validatorProps = { touched, errors };
            const isEmailMethod = values.contactMethod_id === EMAIL_METHOD_ID;
            const isPhoneMethod = values.contactMethod_id === PHONE_METHOD_ID;
            const chosenNumber = clintContactsOptions?.find(
              (option: any) => option.value === values.contact
            )?.label;
            const statusOptions = statusesFor(values.comment_reachability_id);
            const needsPromise = statusRequiresPromise(
              commentsStatusOptions,
              values.comment_status_id,
              values.comment_sub_status_id
            );

            return (
              <Form>
                <FormContainer>
                  <FormDesription title={t("comments.add")} />

                  <div className="grid grid-cols-2 gap-4">
                    {/* Contact Method */}
                    <FormRow
                      border={false}
                      name="contactMethod_id"
                      label={t("comments.contactMethod")}
                      {...validatorProps}
                    >
                      <Field name="contactMethod_id">
                        {({ field, form }: FieldProps) => (
                          <Select
                            field={field}
                            form={form}
                            isLoading={contactMethodsLoading}
                            options={contactMethodsOptions}
                            value={contactMethodsOptions?.filter(
                              (option) =>
                                option.value === values.contactMethod_id
                            )}
                            onChange={(option) => {
                              form.setFieldValue(field.name, option?.value);
                              // numbers and e-mails are different lists
                              if (
                                (option?.value === EMAIL_METHOD_ID) !==
                                (values.contactMethod_id === EMAIL_METHOD_ID)
                              ) {
                                form.setFieldValue("contact", "");
                                form.setFieldValue("call_id", "");
                              }
                            }}
                            placeholder={t("select")}
                          />
                        )}
                      </Field>
                    </FormRow>

                    {/* Contact */}
                    <FormRow
                      border={false}
                      name="contact"
                      label={isEmailMethod ? t("email") : t("phone")}
                      {...validatorProps}
                    >
                      <Field name="contact">
                        {({ field, form }: FieldProps) => (
                          <Select
                            field={field}
                            form={form}
                            isLoading={clintContactsLoading}
                            options={contactsFor(values.contactMethod_id)}
                            value={clintContactsOptions?.filter(
                              (option) => option.value === values.contact
                            )}
                            onChange={(option) => {
                              form.setFieldValue(field.name, option?.value);
                              form.setFieldValue("call_id", "");
                            }}
                            placeholder={t("select")}
                            id="contactNumber"
                          />
                        )}
                      </Field>
                    </FormRow>

                    {/* Call Selection (only for phone method) */}
                    {isPhoneMethod && (
                      <FormRow
                        border={false}
                        name="call_id"
                        label={t("comments.call")}
                        {...validatorProps}
                      >
                        <Field name="call_id">
                          {({ field, form }: FieldProps) => (
                            <Select
                              field={field}
                              form={form}
                              options={callOptionsFor(chosenNumber)}
                              value={callOptionsFor(chosenNumber).filter(
                                (option) => option.value === values.call_id
                              )}
                              onChange={(option) =>
                                form.setFieldValue(field.name, option?.value)
                              }
                              placeholder={t("select")}
                            />
                          )}
                        </Field>
                      </FormRow>
                    )}

                    {/* Comment Reachability */}
                    <FormRow
                      border={false}
                      name="comment_reachability_id"
                      label={t("comments.reachability")}
                      {...validatorProps}
                    >
                      <Field name="comment_reachability_id">
                        {({ field, form }: FieldProps) => (
                          <Select
                            field={field}
                            form={form}
                            isLoading={commentReachabilityLoading}
                            options={commentsReachabilityOptions}
                            value={commentsReachabilityOptions?.filter(
                              (option) =>
                                option.value === values.comment_reachability_id
                            )}
                            onChange={(option) => {
                              form.setFieldValue(field.name, option?.value);
                              // a status that does not fit the new reachability is cleared
                              if (
                                values.comment_status_id &&
                                !statusesFor(option?.value).some(
                                  (s: any) => s.value === values.comment_status_id
                                )
                              ) {
                                form.setFieldValue("comment_status_id", "");
                                form.setFieldValue("comment_sub_status_id", "");
                              }
                            }}
                            placeholder={t("select")}
                          />
                        )}
                      </Field>
                    </FormRow>

                    {/* Comment Status */}
                    <FormRow
                      border={false}
                      name="comment_status_id"
                      label={t("comments.status")}
                      {...validatorProps}
                    >
                      <Field name="comment_status_id">
                        {({ field, form }: FieldProps) => (
                          <Select
                            field={field}
                            form={form}
                            isLoading={commentStatusLoading}
                            options={statusOptions}
                            value={commentsStatusOptions?.filter(
                              (option) =>
                                option.value === values.comment_status_id
                            )}
                            onChange={(option) => {
                              form.setFieldValue(field.name, option?.value);
                              form.setFieldValue("comment_sub_status_id", "");
                            }}
                            placeholder={t("select")}
                          />
                        )}
                      </Field>
                    </FormRow>

                    {/* Sub-status (0068): only when the chosen status has some */}
                    {subStatusesOf(
                      commentsStatusOptions,
                      values.comment_status_id
                    ).length > 0 && (
                      <FormRow
                        border={false}
                        name="comment_sub_status_id"
                        label={t("p7.status.subStatus")}
                        {...validatorProps}
                      >
                        <Field name="comment_sub_status_id">
                          {({ field, form }: FieldProps) => {
                            const subOptions = subStatusesOf(
                              commentsStatusOptions,
                              values.comment_status_id
                            );
                            return (
                              <Select
                                field={field}
                                form={form}
                                isClearable
                                options={subOptions}
                                value={subOptions.filter(
                                  (option) =>
                                    option.value ===
                                    values.comment_sub_status_id
                                )}
                                onChange={(option) =>
                                  form.setFieldValue(
                                    field.name,
                                    option?.value || ""
                                  )
                                }
                                placeholder={t("p7.status.subStatusOptional")}
                              />
                            );
                          }}
                        </Field>
                      </FormRow>
                    )}

                    {/* 0079: the required promise of a promise-to-pay status */}
                    {needsPromise && (
                      <div className="col-span-2">
                        <RequiredPromiseFields
                          caseId={clint?.id ? String(clint.id) : null}
                          value={reqPromise}
                          onChange={setReqPromise}
                          reason="status"
                          showErrors={promiseTried}
                          onOutstanding={setPromiseOutstanding}
                          testId="p10-note-promise"
                        />
                      </div>
                    )}

                    {/* Next Contact Date */}
                    <FormRow
                      border={false}
                      name="nextContactDate"
                      label={t("comments.nextContactDate")}
                      {...validatorProps}
                    >
                      <Field
                        name="nextContactDate"
                        type="date"
                        component={Input}
                        placeholder={t("comments.nextContactDate")}
                      />
                    </FormRow>

                    {/* Next Contact Time */}
                    <FormRow
                      border={false}
                      name="nextContactTime"
                      label={t("comments.nextContactTime")}
                      {...validatorProps}
                    >
                      <Field
                        name="nextContactTime"
                        type="time"
                        component={Input}
                        placeholder={t("comments.nextContactTime")}
                      />
                    </FormRow>
                    {/*  delinquencyReasons  */}
                    <div>
                      <FormRow
                        border={false}
                        name="delinquencyReason_id"
                        label={t(
                          "settings.general.delinquencyReasons.delinquencyReason"
                        )}
                        {...validatorProps}
                      >
                        <Field name="delinquencyReason_id">
                          {({ field, form }: FieldProps) => (
                            <Select
                              field={field}
                              form={form}
                              isLoading={delinquencyReasonsLoading}
                              options={delinquencyReasonsOptions}
                              value={delinquencyReasonsOptions?.filter(
                                (option) =>
                                  option.value === values.delinquencyReason_id
                              )}
                              onChange={(option) =>
                                form.setFieldValue(field.name, option?.value)
                              }
                              placeholder={t("select")}
                            />
                          )}
                        </Field>
                      </FormRow>
                    </div>

                    {/* Comment */}
                    <div className="col-span-2">
                      <FormRow
                        border={false}
                        name="comment"
                        label={t("comments.comment")}
                        {...validatorProps}
                      >
                        <Field
                          name="comment"
                          component={Input}
                          textArea
                          placeholder={t("comments.comment")}
                        />
                      </FormRow>
                    </div>
                  </div>

                  <div className="mt-6 flex justify-end">
                    <Button
                      variant="solid"
                      loading={isSubmitting}
                      type="submit"
                      disabled={clint?.is_archive || clint?.is_locked}
                    >
                      {t("save")}
                    </Button>
                  </div>
                </FormContainer>
              </Form>
            );
          }}
        </Formik>
      </Card>
      )}

      <TimeLineComment data={data} />
    </div>
  );
};

export default Comments;
