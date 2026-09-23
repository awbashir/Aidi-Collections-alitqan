import AdaptableCard from "@/components/shared/AdaptableCard";
import PortfoliosTable from "./list/PortfoliosTable";
import PortfoliosTableTools from "./list/PortfoliosTableTools";
import { injectReducer } from "@/store";
import reducer, { SLICE_NAME } from "./list/store";
import { useState } from "react";

injectReducer(SLICE_NAME, reducer);

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
