import AdaptableCard from "@/components/shared/AdaptableCard";
import PortfoliosTable from "./list/PortfoliosTable";
import PortfoliosTableTools from "./list/PortfoliosTableTools";
import { injectReducer } from "@/store";
import reducer from "./list/store";

injectReducer("portfoliosList", reducer);

const Portfolios = () => {
  return (
    <>
      <AdaptableCard className="h-full" bodyClass="h-full">
        <PortfoliosTableTools />
        <PortfoliosTable />
      </AdaptableCard>
    </>
  );
};

export default Portfolios;
