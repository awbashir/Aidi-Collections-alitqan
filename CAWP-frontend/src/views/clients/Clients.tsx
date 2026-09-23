import AdaptableCard from "@/components/shared/AdaptableCard";
import ClientsTable from "./list/ClientsTable";
import ClientsTableTools from "./list/ClientsTableTools";
import { injectReducer } from "@/store";
import reducer from "./list/store";

injectReducer("clientsList", reducer);

const Clients = () => {
  return (
    <>
      <AdaptableCard className="h-full" bodyClass="h-full">
        <ClientsTableTools />
        <ClientsTable />
      </AdaptableCard>
    </>
  );
};

export default Clients;
