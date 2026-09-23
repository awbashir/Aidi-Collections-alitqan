import React, { useState, useEffect } from "react";
import { useTranslation } from "react-i18next";
import { Card } from "@/components/ui/Card";
import Loading from "@/components/shared/Loading";
import {
  HiOutlineLockClosed,
  HiOutlineLockOpen,
  HiOutlineUser,
  HiOutlineClock,
  HiOutlineRefresh,
} from "react-icons/hi";
import { format } from "date-fns";
import { apiGetLockHistory } from "@/services/ClientsService";
import { useAppSelector } from "../store";
import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import StatusPill from "@/components/shared/StatusPill";

interface LockHistoryItem {
  id: string;
  action: 'lock' | 'unlock';
  reason: string;
  user: {
    id: string;
    name: string;
  };
  user_id: string;
  clint_id: string;
  created_at: string;
}

interface LockHistoryProps {
  clientId?: number;
}

const LockHistory: React.FC<LockHistoryProps> = ({ clientId }) => {
  const { t } = useTranslation();
  const [lockHistory, setLockHistory] = useState<LockHistoryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Get client ID from store if not provided as prop
  const client = useAppSelector(
    (state) => state.crmCustomerDetails?.data?.clientData
  );
  const currentClientId = clientId || client?.id;

  useEffect(() => {
    if (currentClientId) {
      fetchLockHistory();
    }
  }, [currentClientId]);

  const fetchLockHistory = async () => {
    try {
      setLoading(true);
      setError(null);
      const response = await apiGetLockHistory(currentClientId);
      
      if (response.data && response.data.status) {
        setLockHistory(response.data.history || []);
      } else {
        setError(t("clients.lockHistoryFetchFailed"));
      }
    } catch (err) {
      setError(t("clients.lockHistoryFetchFailed"));
      console.error("Error fetching lock history:", err);
    } finally {
      setLoading(false);
    }
  };

  const formatDate = (dateString: string) => {
    try {
      // Convert from GMT format to Date object
      const date = new Date(dateString);
      return format(date, "dd/MM/yyyy HH:mm");
    } catch {
      return dateString;
    }
  };

  const getActionIcon = (action: string) => {
    return action === "lock" ? (
      <HiOutlineLockClosed aria-hidden className="shrink-0" />
    ) : (
      <HiOutlineLockOpen aria-hidden className="shrink-0" />
    );
  };

  const getActionBadge = (action: string) => {
    return (
      <StatusPill
        tone={action === "lock" ? "danger" : "success"}
        size="md"
        className="gap-2"
      >
        <span className="inline-flex items-center gap-1.5">
          {getActionIcon(action)}
          {t(`clients.${action}ed_1`)}
        </span>
      </StatusPill>
    );
  };

  if (loading) {
    return (
      <div className="flex justify-center items-center h-32">
        <Loading loading={true} />
      </div>
    );
  }

  if (error) {
    return (
      <Card>
        <div className="text-center">
          <p className="text-[15px] text-danger">{error}</p>
          <div className="mt-4 flex justify-center">
            <Button
              size="sm"
              variant="default"
              icon={<HiOutlineRefresh />}
              onClick={fetchLockHistory}
            >
              {t("retry")}
            </Button>
          </div>
        </div>
      </Card>
    );
  }

  if (lockHistory.length === 0) {
    return (
      <Card>
        <div className="py-6 text-center text-gray-600">
          <HiOutlineLockClosed
            aria-hidden
            className="mx-auto mb-4 h-12 w-12 text-gray-400"
          />
          <p className="text-[15px]">{t("clients.noLockHistory")}</p>
        </div>
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-4">
        <h3 className="mb-0 text-[15px] font-extrabold text-navy-600">
          {t("clients.lockHistory")}
        </h3>
        <Button
          size="sm"
          variant="default"
          icon={<HiOutlineRefresh />}
          onClick={fetchLockHistory}
        >
          {t("refresh")}
        </Button>
      </div>

      <div className="space-y-4">
        {lockHistory.map((item, index) => (
          <Card
            key={item.id}
            className={`border-s-4 ${
              item.action === "lock" ? "border-s-danger" : "border-s-success"
            }`}
          >
            <div className="flex items-start justify-between">
              <div className="flex-1">
                <div className="flex items-center gap-3 mb-2">
                  {getActionBadge(item.action)}
                  <div className="flex items-center gap-1.5 text-[13.5px] text-gray-600">
                    <HiOutlineClock aria-hidden className="shrink-0" />
                    <span className="tabular-nums">
                      {formatDate(item.created_at)}
                    </span>
                  </div>
                </div>

                <div className="mb-3 flex items-center gap-2">
                  <HiOutlineUser aria-hidden className="shrink-0 text-gray-500" />
                  <span className="text-[13.5px] font-extrabold text-navy-600">
                    {item.user.name}
                  </span>
                </div>

                {item.reason && (
                  <div className="rounded-md border border-gray-200 bg-gray-50 p-3">
                    <p className="text-eyebrow mb-1">{t("reason")}:</p>
                    <p className="text-[13.5px] text-gray-700">{item.reason}</p>
                  </div>
                )}
              </div>
            </div>
          </Card>
        ))}
      </div>
    </div>
  );
};

export default LockHistory;