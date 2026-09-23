import { useEffect } from "react";
import Card from "@/components/ui/Card";
import Avatar from "@/components/ui/Avatar";
import GrowShrinkTag from "@/components/shared/GrowShrinkTag";
import MediaSkeleton from "@/components/shared/loaders/MediaSkeleton";
import Loading from "@/components/shared/Loading";
import { getUserStatistic, useAppDispatch, useAppSelector } from "../store";
import {
  HiOutlineUserGroup,
  HiOutlineUserAdd,
  HiOutlineUsers,
} from "react-icons/hi";
import { NumericFormat } from "react-number-format";
import type { ReactNode } from "react";

type StatisticCardProps = {
  icon: ReactNode;
  avatarClass: string;
  label: string;
  value?: number;
  growthRate?: number;
  loading: boolean;
};

const StatisticCard = (props: StatisticCardProps) => {
  const { icon, avatarClass, label, value, growthRate, loading } = props;

  const avatarSize = 55;

  return (
    <Card bordered>
      <Loading
        loading={loading}
        customLoader={
          <MediaSkeleton
            avatarProps={{
              className: "rounded",
              width: avatarSize,
              height: avatarSize,
            }}
          />
        }
      >
        <div className="flex justify-between items-center">
          <div className="flex items-center gap-4">
            <Avatar className={avatarClass} size={avatarSize} icon={icon} />
            <div>
              <span className="text-eyebrow">{label}</span>
              <h3 className="text-[32px] font-extrabold tabular-nums tracking-[-0.02em] text-navy-600 dark:text-night-body">
                <NumericFormat
                  thousandSeparator
                  displayType="text"
                  value={value}
                />
              </h3>
            </div>
          </div>
          <GrowShrinkTag value={growthRate} suffix="%" />
        </div>
      </Loading>
    </Card>
  );
};

const UserStatistic = () => {
  const dispatch = useAppDispatch();

  const statisticData = useAppSelector(
    (state) => state.crmUsers.data.statisticData
  );
  const loading = useAppSelector(
    (state) => state.crmUsers.data.statisticLoading
  );

  useEffect(() => {
    dispatch(getUserStatistic());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-4 mb-6">
      <StatisticCard
        icon={<HiOutlineUserGroup />}
        avatarClass="!bg-indigo-600"
        label="Total Users"
        value={statisticData?.totalUsers?.value}
        growthRate={statisticData?.totalUsers?.growShrink}
        loading={loading}
      />
      <StatisticCard
        icon={<HiOutlineUsers />}
        avatarClass="!bg-blue-500"
        label="Active Users"
        value={statisticData?.activeUsers?.value}
        growthRate={statisticData?.activeUsers?.growShrink}
        loading={loading}
      />
      <StatisticCard
        icon={<HiOutlineUserAdd />}
        avatarClass="!bg-emerald-500"
        label="New Users"
        value={statisticData?.newUsers?.value}
        growthRate={statisticData?.newUsers?.growShrink}
        loading={loading}
      />
    </div>
  );
};

export default UserStatistic;
