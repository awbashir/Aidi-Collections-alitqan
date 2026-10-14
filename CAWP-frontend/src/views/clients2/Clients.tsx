import AdaptableCard from "@/components/shared/AdaptableCard";
import ClientsTable from "./list/ClientsTable";
import ClientsTableTools from "./list/ClientsTableTools";
import { injectReducer } from "@/store";
import reducer from "./list/store";
import { useClientQueryHashSync } from "./list/hooks/useClientQueryHashSync";

injectReducer("clientsList2", reducer);

const Clients = () => {
  /* Page, sort and the search fields live in the URL hash; the list mounts
   * once the hash is in the store, so its first request uses it. */
  const ready = useClientQueryHashSync();

  return (
    <>
      <AdaptableCard className="h-full" bodyClass="h-full">
        {ready && (
          <>
            <ClientsTableTools />
            <ClientsTable />
          </>
        )}
      </AdaptableCard>
    </>
  );
};

export default Clients;
