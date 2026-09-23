import AdaptableCard from "@/components/shared/AdaptableCard";
import ClientsTable from "./list/ClientsTable";
import ClientsTableTools from "./list/ClientsTableTools";
import { injectReducer } from "@/store";
import reducer from "./list/store";
import { useClientsListHashSync } from "./list/hooks/useClientsHashState";

injectReducer("clientsList", reducer);

const Clients = () => {
  /* Filters, sort, page, page size and the last opened case live in the URL
   * hash. The list mounts once the hash is in the store, so its first request
   * already uses the restored state. */
  const ready = useClientsListHashSync();

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
