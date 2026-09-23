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
//const Messages = lazy(() => import("./tabs/Messages")); // This line is missing in the original file
const CallsRecord = lazy(() => import("./tabs/CallsRecord"));
const LockHistory = lazy(() => import("./tabs/LockHistory"));
const Documents = lazy(() => import("./tabs/Documents"));

const { TabNav, TabList } = Tabs;

const Settings = () => {
  const { t } = useTranslation();
  const query = useQuery();
  const dispatch = useAppDispatch();
  const { permissions } = useAppSelector((state) => state.auth.user);

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
          </Suspense>
        </div>
      </AdaptableCard>
    </Container>
  );
};

export default Settings;
