import { useState, useEffect, Suspense, lazy } from "react";
import Tabs from "@/components/ui/Tabs";
import AdaptableCard from "@/components/shared/AdaptableCard";
import Container from "@/components/shared/Container";
import { useNavigate, useLocation } from "react-router-dom";
import isEmpty from "lodash/isEmpty";

import {
  apiGetClientsDetails,
  apiGetPaymentsClientsTotal,
  apiGetContactsIdentityNumber,
} from "@/services/ClientsService";
import {
  getCustomer,
  getComments,
  getContactsIdentityNumber,
  useAppDispatch,
  useAppSelector,
} from "./store";
import { useTranslation } from "react-i18next";
import useQuery from "@/utils/hooks/useQuery";
import { injectReducer } from "@/store";
import reducer from "./store";
import Alert from "@/components/ui/Alert";
import StatusPill from "@/components/shared/StatusPill";
import type { CaseInfo } from "@/views/phase1/caseTypes";
import Button from "@/components/ui/Button";
import { historyApi } from "@/services/v2";
import { useV2Query } from "@/utils/v2/hooks";

injectReducer("crmCustomerDetails", reducer);

type AccountSetting = {
  details: {
    id: string;
    name: string;
    email: string;
    title: string;
    avatar: string;
    timeZone: string;
    lang: string;
    syncData: boolean;
  };
  loginHistory: {
    type: string;
    deviceName: string;
    time: number;
    location: string;
  }[];
  comments: {
    news: string[];
    accountActivity: string[];
    signIn: string[];
    reminders: string[];
    mentioned: string[];
    replies: string[];
    taskUpdate: string[];
    assigned: string[];
    newProduct: string[];
    newOrder: string[];
  };
};

type GetAccountSettingData = AccountSetting;

const Details = lazy(() => import("./tabs/Details"));
const UpdateData = lazy(() => import("./tabs/UpdateData"));
const CommentsSetting = lazy(() => import("./tabs/Comments"));
const Payments = lazy(() => import("./tabs/Payments"));
const PaymentsDate = lazy(() => import("./tabs/PaymentsDate"));
const UsersArchive = lazy(() => import("./tabs/UsersArchive"));
//const Messages = lazy(() => import("./tabs/messages")); // This line is missing in the original file
const CallsRecord = lazy(() => import("./tabs/CallsRecord"));
const LockHistory = lazy(() => import("./tabs/LockHistory"));
const Documents = lazy(() => import("./tabs/Documents"));
/* Phase 1 panels (v2 API). Each checks its own permission as well. */
const CaseFollowUpsPanel = lazy(
  () => import("@/views/phase1/followUps/CaseFollowUpsPanel"),
);
const CasePromisesPanel = lazy(
  () => import("@/views/phase1/money/CasePromisesPanel"),
);
const CaseCorrectionsPanel = lazy(
  () => import("@/views/phase1/history/CaseCorrectionsPanel"),
);
const RequestCorrectionDialog = lazy(
  () => import("@/views/phase1/history/RequestCorrectionDialog"),
);
/* Phase 3 panels (v2 API). Each checks its own permission as well. */
const CasePlanPanel = lazy(() => import("@/views/phase3/plans/CasePlanPanel"));
const CaseRequestsPanel = lazy(
  () => import("@/views/phase3/requests/CaseRequestsPanel"),
);
/* Phase 5 panels (v2 API): the case's calls and the manual call log. */
const CaseCallsPanel = lazy(() => import("@/views/phase5/case/CaseCallsPanel"));
const LogCallDialog = lazy(() => import("@/views/phase5/wrapup/LogCallDialog"));

const { TabNav, TabList } = Tabs;

const Settings = () => {
  const { t } = useTranslation();
  const query = useQuery();
  const dispatch = useAppDispatch();
  const { permissions } = useAppSelector((state) => state.auth.user);
  /* Phase 1 reads its keys through a typed view of the same map. */
  const p1perm = (permissions ?? {}) as Record<string, boolean>;
  const canSeePlans = !!(p1perm.plans || p1perm.plans_manage);
  const canSeeRequests = !!(
    p1perm.requests ||
    p1perm.requests_legal ||
    p1perm.requests_finance ||
    p1perm.requests_operations
  );
  /* Phase 5: the case's calls (calls | calls_make); "Log a call" needs `calls`. */
  const canSeeCalls = !!(p1perm.calls || p1perm.calls_make);

  const settingsMenu: Record<
    string,
    {
      label: string;
      path: string;
    }
  > = {
    details: { label: t("details"), path: "details" },
    updateData: { label: t("updateData"), path: "updateData" },
    comments: { label: t("comments.title"), path: "comments" },
    payments: { label: t("payments.title"), path: "payments" },
    paymentsDate: { label: t("paymentsDate.title"), path: "paymentsDate" },
    usersArchive: { label: t("usersArchive.title"), path: "usersArchive" },
    //messages: { label: t("messages"), path: "messages" },
    ...(permissions.clints_callsRecords && {
      callsRecord: { label: t("callsRecord.title"), path: "callsRecord" },
    }),
    ...(permissions.clints_lock_history && {
      lockHistory: { label: t("lockHistory"), path: "lockHistory" },
    }),
    ...(permissions.clints_documents && {
      // ← جديد
      documents: { label: t("documents.title"), path: "documents" },
    }),
    /* Phase 1 tabs, gated by their permission keys. */
    ...(p1perm.follow_ups && {
      followUps: { label: t("p1.case.tabFollowUps"), path: "followUps" },
    }),
    ...((p1perm.promises || p1perm.clints_PayemntDates) && {
      promises: { label: t("p1.case.tabPromises"), path: "promises" },
    }),
    ...((p1perm.case_corrections_request || p1perm.case_corrections_review) && {
      corrections: { label: t("p1.case.tabCorrections"), path: "corrections" },
    }),
    /* Phase 3 tabs: the repayment plan (plans | plans_manage) and internal
     * requests (requests | requests_<department>). */
    ...(canSeePlans && {
      plan: { label: t("p3.case.tabPlan"), path: "plan" },
    }),
    ...(canSeeRequests && {
      requests: { label: t("p3.case.tabRequests"), path: "requests" },
    }),
    /* Phase 5 tab: the case's calls, wrap-ups, do-not-call block. */
    ...(canSeeCalls && {
      calls: { label: t("p5.case.tabCalls"), path: "calls" },
    }),
  };
  const [currentTab, setCurrentTab] = useState("details");
  //const [data, setData] = useState<Partial<AccountSetting>>({});
  const [total_paments, setTotal_paments] = useState(0);
  //const [clintContacts, setClintContacts] = useState([]);

  const navigate = useNavigate();

  const location = useLocation();
  const data = useAppSelector(
    (state) => state.crmCustomerDetails?.data?.clientData,
  );
  const clintContacts = useAppSelector(
    (state) => state.crmCustomerDetails?.data?.ContactData,
  );

  /* `data` is typed `Partial<User>` in this legacy slice but is actually the
     client record; narrow the one identifier that is not on that type. */
  const accountNumber = (data as { accountNumber?: string })?.accountNumber;

  const path = location.pathname.substring(
    location.pathname.lastIndexOf("/") + 1,
  );

  const onTabChange = (val: string) => {
    setCurrentTab(val);
    navigate(`/clients/details/${val}`);
  };

  const fetchData = async () => {
    let id = query.get("id");

    if (id) {
      // If id is not null, save it to local storage
      localStorage.setItem("id", id);
    } else {
      // If id is null, try to get it from local storage
      const storedId = localStorage.getItem("id");
      if (storedId) {
        // If storedId is not null, assign it to id
        id = storedId;
      }
    }

    //const response = await apiGetClientsDetails<GetAccountSettingData>(id);
    dispatch(getCustomer(id));

    const total_paments = await apiGetPaymentsClientsTotal(id);

    //setData(response.data);
    setTotal_paments(total_paments.data.total);
  };

  /* Phase 1: after a v2 mutation, reload what the legacy panels show (case,
   * notes, contacts, paid total) and bump the key the v2 panels watch. */
  const [p1RefreshKey, setP1RefreshKey] = useState(0);
  const onPhase1Changed = () => {
    const id = data?.id;
    if (!id) return;
    /* The legacy thunks are typed loosely; they take the bare id. */
    dispatch(getCustomer(id as never));
    dispatch(getComments(id as never));
    if (data?.identityNumber) {
      dispatch(getContactsIdentityNumber(data.identityNumber as never));
    }
    apiGetPaymentsClientsTotal<{ total: number }, never>(id as never)
      .then((res) => setTotal_paments(res.data.total))
      .catch(() => undefined);
    setP1RefreshKey((n) => n + 1);
  };
  const caseInfo = (data?.id ? data : null) as CaseInfo | null;
  const [requestOpen, setRequestOpen] = useState(false);
  const [logCallOpen, setLogCallOpen] = useState(false);
  /* Phase 5: a wrap-up saved anywhere (the docked panel) on this case wrote a
   * note: reload what the legacy panels show, like any v2 mutation. */
  useEffect(() => {
    const onWrapped = (e: Event) => {
      const d = (e as CustomEvent<{ caseId: string | null }>).detail;
      if (d && caseInfo?.id && d.caseId === caseInfo.id) onPhase1Changed();
    };
    window.addEventListener("cawp:call-wrapped", onWrapped);
    return () => window.removeEventListener("cawp:call-wrapped", onWrapped);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [caseInfo?.id]);
  const canSeeCorrections =
    p1perm.case_corrections_request || p1perm.case_corrections_review;
  /* Pending corrections on this case (exact count), shown under the header. */
  const pendingCorrections = useV2Query(
    canSeeCorrections && caseInfo?.id
      ? (signal) =>
          historyApi.corrections(
            { case_id: caseInfo.id, status: "pending", page_size: 1 },
            signal,
          )
      : null,
    [caseInfo?.id, canSeeCorrections, p1RefreshKey],
  );
  const pendingCount = pendingCorrections.data?.total ?? 0;

  useEffect(() => {
    setCurrentTab(path);

    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentTab]);

  useEffect(() => {
    fetchData();
  }, []);

  /*   const fetchContacts = async (identityNumber: string) => {
    const response = await apiGetContactsIdentityNumber(identityNumber);
    setClintContacts(response.data.contacts);
  }; */

  useEffect(() => {
    if (data.status == false) {
      navigate(`/access-denied`);
    }
    if (data?.identityNumber) {
      // fetchContacts(data.identityNumber);
      dispatch(getContactsIdentityNumber(data.identityNumber));
    }
  }, [data]);

  return (
    <Container>
      <AdaptableCard>
        <div className="mb-8">
          <span className="text-eyebrow">اسم العميل</span>
          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-2">
            <h3 className="mb-0 text-[20px] font-extrabold text-navy-600">
              {data?.name}
            </h3>
            {data?.is_archive && (
              <StatusPill tone="neutral" size="md">
                {t("archived")}
              </StatusPill>
            )}
          </div>
          <div className="mt-1.5 flex flex-wrap items-center gap-x-6 gap-y-1 text-[13.5px] text-gray-600">
            {accountNumber && (
              <span className="inline-flex max-w-full items-center gap-2">
                <span className="shrink-0 text-gray-500">
                  {t("clients.accountNumber")}
                </span>
                <span
                  dir="ltr"
                  title={String(accountNumber)}
                  className="truncate tabular-nums text-gray-700"
                >
                  {accountNumber}
                </span>
              </span>
            )}
            {data?.identityNumber && (
              <span className="inline-flex max-w-full items-center gap-2">
                <span className="shrink-0 text-gray-500">
                  {t("clients.identityNumber")}
                </span>
                <span
                  dir="ltr"
                  title={String(data.identityNumber)}
                  className="truncate tabular-nums text-gray-700"
                >
                  {data.identityNumber}
                </span>
              </span>
            )}
          </div>
          {caseInfo &&
            (p1perm.case_corrections_request || pendingCount > 0 || p1perm.calls) && (
            <div className="mt-3 flex flex-wrap items-center gap-3">
              {p1perm.calls && (
                <Button
                  size="sm"
                  onClick={() => setLogCallOpen(true)}
                  data-testid="p5-log-call"
                >
                  {t("p5.log.open")}
                </Button>
              )}
              {p1perm.case_corrections_request && (
                <Button
                  size="sm"
                  onClick={() => setRequestOpen(true)}
                  data-testid="p1-request-correction"
                >
                  {t("p1.case.requestCorrection")}
                </Button>
              )}
              {pendingCount > 0 && (
                <button
                  type="button"
                  className="text-[13px] font-extrabold text-azure-600 hover:underline"
                  onClick={() => onTabChange("corrections")}
                  data-testid="p1-pending-corrections"
                >
                  {t("p1.case.pendingCorrections", { count: pendingCount })}
                </button>
              )}
            </div>
          )}
          {data?.is_locked && (
            <Alert type="warning" showIcon className="mt-4" duration={0}>
              {t("clients.locked")}
            </Alert>
          )}
        </div>
        <Tabs value={currentTab} onChange={(val) => onTabChange(val)}>
          <TabList>
            {Object.keys(settingsMenu).map((key) => (
              <TabNav key={key} value={key}>
                {settingsMenu[key].label}
              </TabNav>
            ))}
          </TabList>
        </Tabs>
        <div className="pt-8">
          <Suspense fallback={<></>}>
            {currentTab === "details" && data !== null && (
              <Details
                data={data}
                total_paments={total_paments}
                clintContacts={clintContacts}
                p1RefreshKey={p1RefreshKey}
                onPhase1Changed={onPhase1Changed}
              />
            )}
            {currentTab === "updateData" && data !== null && (
              <UpdateData data={data} tap={onTabChange} />
            )}
            {currentTab === "comments" && (
              <CommentsSetting data={data.comments} />
            )}
            {currentTab === "payments" && data !== null && <Payments />}
            {currentTab === "paymentsDate" && data !== null && <PaymentsDate />}
            {currentTab === "usersArchive" && data !== null && <UsersArchive />}
            {/* {currentTab === "messages" && <Messages />} */}
            {currentTab === "callsRecord" &&
              data !== null &&
              permissions.clints_callsRecords && <CallsRecord />}
            {currentTab === "lockHistory" &&
              data !== null &&
              permissions.clints_lock_history && <LockHistory />}
            {currentTab === "documents" &&
              data !== null &&
              permissions.clints_documents && <Documents />}{" "}
            {/* ← جديد */}
            {currentTab === "followUps" &&
              caseInfo &&
              p1perm.follow_ups && (
                <CaseFollowUpsPanel
                  key={caseInfo.id}
                  caseData={caseInfo}
                  onChanged={onPhase1Changed}
                />
              )}
            {currentTab === "corrections" && caseInfo && canSeeCorrections && (
              <CaseCorrectionsPanel
                key={caseInfo.id}
                caseData={caseInfo}
                onChanged={onPhase1Changed}
              />
            )}
            {currentTab === "plan" && caseInfo && canSeePlans && (
              <CasePlanPanel
                key={caseInfo.id}
                caseData={caseInfo}
                onChanged={onPhase1Changed}
              />
            )}
            {currentTab === "requests" && caseInfo && canSeeRequests && (
              <CaseRequestsPanel
                key={caseInfo.id}
                caseData={caseInfo}
                onChanged={onPhase1Changed}
              />
            )}
            {currentTab === "calls" && caseInfo && canSeeCalls && (
              <CaseCallsPanel
                key={caseInfo.id}
                caseData={caseInfo}
                onChanged={onPhase1Changed}
              />
            )}
            {currentTab === "promises" &&
              caseInfo &&
              (p1perm.promises || p1perm.clints_PayemntDates) && (
                <CasePromisesPanel
                  key={caseInfo.id}
                  caseData={caseInfo}
                  onChanged={onPhase1Changed}
                />
              )}
          </Suspense>
        </div>
        {caseInfo && logCallOpen && (
          <Suspense fallback={null}>
            <LogCallDialog
              isOpen={logCallOpen}
              onClose={() => setLogCallOpen(false)}
              caseInfo={{
                id: caseInfo.id,
                name: caseInfo.name,
                accountNumber: caseInfo.accountNumber,
                identityNumber: caseInfo.identityNumber,
                contractId: caseInfo.contract?.id || null,
                isLocked: !!caseInfo.is_locked,
                isArchived: !!caseInfo.is_archive,
              }}
            />
          </Suspense>
        )}
        {caseInfo && requestOpen && (
          <Suspense fallback={null}>
            <RequestCorrectionDialog
              isOpen={requestOpen}
              onClose={() => setRequestOpen(false)}
              caseData={caseInfo}
              onDone={onPhase1Changed}
            />
          </Suspense>
        )}
      </AdaptableCard>
    </Container>
  );
};

export default Settings;
