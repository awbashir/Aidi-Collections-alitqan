import AdaptableCard from "@/components/shared/AdaptableCard";
import UsersTable from "./components/UsersTable";
import UsersTableTools from "./components/UsersTableTools";
import UserStatistic from "./components/UsersStatistic";
import { injectReducer } from "@/store";
import reducer from "./store";

injectReducer("crmUsers", reducer);

const Users = () => {
  return (
    <>
      {/*  <UserStatistic /> */}
      <AdaptableCard className="h-full" bodyClass="h-full">
        <UsersTableTools />
        <UsersTable />
      </AdaptableCard>
    </>
  );
};

export default Users;
