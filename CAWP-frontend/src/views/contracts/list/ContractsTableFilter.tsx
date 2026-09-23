import Select from "@/components/ui/Select";
import Badge from "@/components/ui/Badge";
import { setFilterData, useAppDispatch, useAppSelector } from "./store";
import {
  components,
  ControlProps,
  OptionProps,
  SingleValue,
} from "react-select";
import { HiCheck } from "react-icons/hi";
import { useTranslation } from "react-i18next";

type Option = {
  value: string;
  label: string;
  color: string;
};

const { Control } = components;

const CustomSelectOption = ({
  innerProps,
  label,
  data,
  isSelected,
}: OptionProps<Option>) => {
  return (
    <div
      className={`flex items-center justify-between p-2 cursor-pointer ${
        isSelected
          ? "bg-gray-100 dark:bg-gray-500"
          : "hover:bg-gray-50 dark:hover:bg-gray-600"
      }`}
      {...innerProps}
    >
      <div className="flex items-center gap-2">
        <Badge color={data.color} />
        <span>{label}</span>
      </div>
      {isSelected && <HiCheck className="text-emerald-500 text-xl" />}
    </div>
  );
};

const CustomControl = ({ children, ...props }: ControlProps<Option>) => {
  const selected = props.getValue()[0];
  return (
    <Control {...props}>
      {selected && (
        <Badge className="ltr:ml-4 rtl:mr-4" color={selected.color} />
      )}
      {children}
    </Control>
  );
};

const ContractsTableFilter = () => {
  const dispatch = useAppDispatch();
  const { t } = useTranslation();

  const options_status: Option[] = [
    { value: 0, label: t("all"), color: "bg-gray-500" },
    { value: true, label: t("active"), color: "bg-emerald-500" },
    { value: false, label: t("inactive"), color: "bg-red-500" },
  ];

  const options_arcive: Option[] = [
    { value: 0, label: t("all"), color: "bg-gray-500" },
    { value: true, label: t("archived"), color: "bg-red-500" },
    { value: false, label: t("notArchived"), color: "bg-emerald-500" },
  ];

  const {
    customeFilterStatusesValue,
    customeFilterArchivingStatusesValue,
    customeFilterBranchesValue,
  } = useAppSelector((state) => state.contractsList.data.filterData);

  const filterData = useAppSelector(
    (state) => state.contractsList.data.filterData
  );

  const onStatusFilterChange_customeFilterStatusesValue = (
    selected: SingleValue<Option>
  ) => {
    dispatch(
      setFilterData({
        ...filterData,
        customeFilterStatusesValue: selected?.value,
      })
    );
  };

  const onStatusFilterChange_customeFilterArchivingStatusesValue = (
    selected: SingleValue<Option>
  ) => {
    dispatch(
      setFilterData({
        ...filterData,
        customeFilterArchivingStatusesValue: selected?.value,
      })
    );
  };

  return (
    <>
      <div className="">
        <label className=" ">{t("contracts.status")}</label>

        <Select<Option>
          options={options_status}
          size="sm"
          className="min-w-[130px]"
          components={{
            Option: CustomSelectOption,
            Control: CustomControl,
          }}
          value={options_status.filter(
            (option) => option.value === customeFilterStatusesValue
          )}
          onChange={onStatusFilterChange_customeFilterStatusesValue}
        />
      </div>
      <div className="">
        <label className=" ">{t("clients.count")}</label>
        <Select<Option>
          options={options_arcive}
          size="sm"
          className="min-w-[130px]"
          value={options_arcive.filter(
            (option) => option.value === customeFilterArchivingStatusesValue
          )}
          onChange={onStatusFilterChange_customeFilterArchivingStatusesValue}
        />
      </div>
    </>
  );
};

export default ContractsTableFilter;
