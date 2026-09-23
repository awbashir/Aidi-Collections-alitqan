import { useRef } from "react";
import Button from "@/components/ui/Button";
import {
  getClients,
  setTableData,
  setFilterData,
  useAppDispatch,
  useAppSelector,
} from "./store";
import UserTableSearch from "./UserTableSearch";
import ClientsTableFilter from "./ClientsTableFilter";
import cloneDeep from "lodash/cloneDeep";
import type { TableQueries } from "@/@types/common";
import { useTranslation } from "react-i18next";

const ClientsTableTools = () => {
  const dispatch = useAppDispatch();
  const { t } = useTranslation();

  const inputRef = useRef<HTMLInputElement>(null);

  const tableData = useAppSelector((state) => state.clientsList.data.tableData);

  const handleInputChange = (val: string) => {
    const newTableData = cloneDeep(tableData);
    newTableData.query = val;
    newTableData.page = 1;
    if (typeof val === "string" && val.length > 1) {
      fetchData(newTableData);
    }

    if (typeof val === "string" && val.length === 0) {
      fetchData(newTableData);
    }
  };

  const fetchData = (data: TableQueries) => {
    dispatch(setTableData(data));
    dispatch(getUsers(data));
  };

  const onClearAll = () => {
    const newTableData = cloneDeep(tableData);
    newTableData.query = "";
    if (inputRef.current) {
      inputRef.current.value = "";
    }
    dispatch(
      setFilterData({
        customeFilterStatusesValue: 0,
        customeFilterArchivingStatusesValue: 0,
        customeFilterBranchesValue: "",
      })
    );
    fetchData(newTableData);
  };

  return (
    <div>
      <div>
        {/*  <UserTableSearch ref={inputRef} onInputChange={handleInputChange} /> */}
        <ClientsTableFilter />
      </div>
    </div>
  );
};

export default ClientsTableTools;
