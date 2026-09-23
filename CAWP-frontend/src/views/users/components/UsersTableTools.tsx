import { useRef } from "react";
import Button from "@/components/ui/Button";
import {
  getUsers,
  setTableData,
  setFilterData,
  useAppDispatch,
  useAppSelector,
} from "../store";
import UserTableSearch from "./UserTableSearch";
import UserTableFilter from "./UserTableFilter";
import cloneDeep from "lodash/cloneDeep";
import type { TableQueries } from "@/@types/common";
import { useTranslation } from "react-i18next";

const UsersTableTools = () => {
  const dispatch = useAppDispatch();
  const { t } = useTranslation();

  const inputRef = useRef<HTMLInputElement>(null);

  const tableData = useAppSelector((state) => state.crmUsers.data.tableData);

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
    <div className="mb-4 flex flex-wrap items-end gap-2">
      <div className="flex flex-wrap items-end gap-4">
        {/*  <UserTableSearch ref={inputRef} onInputChange={handleInputChange} /> */}
        <UserTableFilter />
      </div>
      <Button variant="default" size="sm" onClick={onClearAll}>
        {t("clearAll")}
      </Button>
    </div>
  );
};

export default UsersTableTools;
