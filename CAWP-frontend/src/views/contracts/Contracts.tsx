import AdaptableCard from "@/components/shared/AdaptableCard";
import ContractsTable from "./list/ContractsTable";
import ContractsTableTools from "./list/ContractsTableTools";
import { injectReducer } from "@/store";
import reducer from "./list/store";

injectReducer("contractsList", reducer);

const Contracts = () => {
  return (
    <>
      <AdaptableCard className="h-full" bodyClass="h-full">
        <ContractsTableTools />
        <ContractsTable />
      </AdaptableCard>
    </>
  );
};

export default Contracts;
