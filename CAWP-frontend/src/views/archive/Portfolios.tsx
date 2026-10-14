import AdaptableCard from "@/components/shared/AdaptableCard";
import PortfoliosTable from "./list/PortfoliosTable";
import PortfoliosTableTools from "./list/PortfoliosTableTools";
import { injectReducer } from "@/store";
import reducer from "./list/store";
import { useArchiveHashSync } from "./list/hooks/useArchiveHashSync";

injectReducer("archiveList", reducer);

const Portfolios = () => {
  /* Page, sort and the search filters live in the URL hash; the list mounts
   * once the hash is in the store, so its first request uses it. */
  const ready = useArchiveHashSync();

  return (
    <>
      <AdaptableCard className="h-full" bodyClass="h-full">
        {ready && (
          <>
            <PortfoliosTableTools />
            <PortfoliosTable />
          </>
        )}
      </AdaptableCard>
    </>
  );
};

export default Portfolios;
