import { useRef } from "react";
import Button from "@/components/ui/Button";
import Checkbox from "@/components/ui/Checkbox";
import {
  getPortfolios,
  setTableData,
  setFilterData,
  setLatestOnly,
  useAppDispatch,
  useAppSelector,
} from "./store";
import UserTableSearch from "./UserTableSearch";
import PortfoliosTableFilter from "./PortfoliosTableFilter";
import cloneDeep from "lodash/cloneDeep";
import type { TableQueries } from "@/@types/common";
import { useTranslation } from "react-i18next";

const PortfoliosTableTools = () => {
  const dispatch = useAppDispatch();
  const { t } = useTranslation();

  const inputRef = useRef<HTMLInputElement>(null);

  const tableData = useAppSelector(
    (state) => state.commentsList.data.tableData,
  );

  const latestOnly = useAppSelector(
    (state) => state.commentsList.data.latestOnly,
  );

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

    fetchData(newTableData);
  };

  return (
    <div /* className="md:flex items-center justify-between" */>
      <div /* className="md:flex items-center gap-4" */>
        {/*  <UserTableSearch ref={inputRef} onInputChange={handleInputChange} /> */}
        <PortfoliosTableFilter />
        <Checkbox
          checked={latestOnly}
          onChange={(checked) => dispatch(setLatestOnly(checked))}
        >
          عرض آخر متابعة لكل عميل فقط
        </Checkbox>
      </div>
      {/*  <div className="mb-4">
        <Button size="sm" onClick={onClearAll}>
          {t("clearAll")}
        </Button>
      </div> */}
    </div>
  );
};

export default PortfoliosTableTools;
