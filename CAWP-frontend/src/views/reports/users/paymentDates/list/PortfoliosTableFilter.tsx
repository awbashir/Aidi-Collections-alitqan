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
import {
  apiGetContracts,
  apiGetPortfolios,
  apiGetCommentReachability,
  apiGetCommentStatus,
  apiGetUsers,
  apiGetArchiveReasons,
  apiGetBranches,
} from "@/services/CollectionsService";
import MyFilter from "@/components/shared/myComponents/filter";
import MyField from "@/components/shared/myComponents/field";
import FilterAmount from "@/components/shared/myComponents/filterAmount";
import Button from "@/components/ui/Button";

type Option = {
  value: string;
  label: string;
  color: string;
};
import { useEffect, useState } from "react";

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

const PortfoliosTableFilter = () => {
  const dispatch = useAppDispatch();
  const search = useAppSelector((state) => state.PaymentDatesList.data.search);

  const { t } = useTranslation();
  const [selectedContracts_, setSelectedContracts_] = useState([]);
  const [selectedPortfolios_, setSelectedPortfolios_] = useState();
  const [selectedReachability_, setSelectedReachability_] = useState();
  const [selectedCommentStatus_, setSelectedCommentStatus_] = useState();
  const [selectedUsers_, setSelectedUsers_] = useState();
  const [selectedAccountNumber_, setSelectedAccountNumber_] = useState();
  const [selectedArchiveBy_, setSelectedArchiveBy_] = useState();
  const [selectedResons_, setSelectedResons_] = useState();
  const [selectedBranch_, setSelectedBranch_] = useState();
  const [selectedAmount_, setSelectedAmount_] = useState();
  const [selectedClientID_, setSelectedClientID_] = useState();

  const [contracts, setContracts] = useState<Contract[]>([]);
  const [portfolios, setPortfolios] = useState<Portfolio[]>([]);
  const [reachability, setReachability] = useState<Reachability[]>([]);
  const [comment_status, setComment_status] = useState<CommentStatus[]>([]);
  const [users, setUsers] = useState<User[]>([]);
  const [archive_reason, setArchive_reason] = useState<ArchiveReason[]>([]);
  const [branches, setBranches] = useState<Branch[]>([]);

  const fetchContracts = async () => {
    const response = await apiGetContracts();
    setContracts(response.data.contracts);
  };

  const fetchPortfolios = async () => {
    const response = await apiGetPortfolios();
    setPortfolios(response.data.portfolios);
  };

  const fetchReachability = async () => {
    const response = await apiGetCommentReachability();
    setReachability(response.data.comment_reachabilitys);
  };

  const fetchCommentStatus = async () => {
    const response = await apiGetCommentStatus();
    setComment_status(response.data.comment_statuses);
  };

  const fetchUsers = async () => {
    const response = await apiGetUsers();
    setUsers(response.data.users);
  };

  const fetchArchiveReasons = async () => {
    const response = await apiGetArchiveReasons();
    setArchive_reason(response.data.reson_archive);
  };

  const fetchBranches = async () => {
    const response = await apiGetBranches();
    setBranches(response.data.branches);
  };

  useEffect(() => {
    fetchContracts();
  }, []);

  useEffect(() => {
    fetchPortfolios();
  }, []);

  useEffect(() => {
    fetchReachability();
  }, []);

  useEffect(() => {
    fetchCommentStatus();
  }, []);

  useEffect(() => {
    fetchUsers();
  }, []);

  useEffect(() => {
    fetchArchiveReasons();
  }, []);

  useEffect(() => {
    fetchBranches();
  }, []);

  const [filteredComment_status, setFilteredComment_status] = useState([]);
  useEffect(() => {
    setSelectedCommentStatus_([]);
    if (selectedReachability_?.length > 0) {
      const commentstatus_ = comment_status.filter((status) =>
        selectedReachability_.some(
          (reachability) => reachability.id === status.CommentReachability_id
        )
      );
      setFilteredComment_status(commentstatus_);
    } else {
      setFilteredComment_status(comment_status);
    }

    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedReachability_, selectedContracts_]);

  const [filteredUsers, setFilteredUsers] = useState([]);
  useEffect(() => {
    setSelectedUsers_([]);
    if (selectedBranch_?.length > 0) {
      const users_ = users.filter((user) =>
        selectedBranch_.some((branch) => branch.id === user.branch_id)
      );
      setFilteredUsers(users_);
    } else {
      setFilteredUsers(users);
    }

    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedBranch_]);

  const filterData = useAppSelector(
    (state) => state.PaymentDatesList.data.filterData
  );

  const handelSearch = () => {
    dispatch(
      setFilterData({
        ...filterData,

        selectedPortfolios_Qkey: selectedPortfolios_?.map(
          (portfolios) => portfolios.id
        ),
        selectedPortfolios_Jkey: "portfolios.id",
        selectedPortfolios_Type: "string",

        //-----------------
        selectedContracts_Qkey: selectedContracts_?.map(
          (contracts) => contracts.id
        ),
        selectedContracts_Jkey: "portfolios.contract.id",
        selectedContracts_Type: "string",

        //-----------------
        selectedUsers_Qkey: selectedUsers_?.map((branches) => branches.id),
        selectedUsers_Jkey: "user_id",
        selectedUsers_Type: "string",

        //-----------------
        selectedBranches_Qkey: selectedBranch_?.map((branches) => branches.id),
        selectedBranches_Jkey: "user.branch_id",
        selectedBranches_Type: "string",

        //-----------------
        selectedClientID_Qkey: selectedClientID_,
        selectedClientID_Jkey: "clint_id",
        selectedClientID_Type: "string",
        //-----------------
        selectedAccountNumber_Qkey: selectedAccountNumber_,
        selectedAccountNumber_Jkey: "clint.accountNumber",
        selectedAccountNumber_Type: "string",

        //-----------------
        selectedAmount_Qkey: selectedAmount_,
        selectedAmount_Jkey: "amount",
        selectedAmount_Type: "number",
        selectedAmount_Operator: "fromTo",
        //-----------------

        selectedReachability_Qkey: selectedReachability_?.map(
          (reachability) => reachability.id
        ),
        selectedReachability_Jkey: "comment.comment_reachability_id",
        selectedReachability_Type: "string",

        //-----------------

        selectedCommentStatus_Qkey: selectedCommentStatus_?.map(
          (status) => status.id
        ),
        selectedCommentStatus_Jkey: "comment.comment_status_id",
        selectedCommentStatus_Type: "string",

        //-----------------
      })
    );
  };

  return (
    <>
      {search ? (
        <div>
          <div className="rounded-t-lg border border-gray-300 bg-gray-100 px-5 py-3 text-[15px] font-extrabold text-navy-600 dark:border-night-border dark:bg-night-raised dark:text-night-body">
            بحث عن السدادت
          </div>

          <div className="rounded-b-lg border border-t-0 border-gray-300 bg-white p-5 dark:border-night-border dark:bg-night-card">
            <div className="grid grid-cols-2 gap-4">
              <div className="rounded-lg border border-gray-300 p-5 dark:border-night-border">
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    {" "}
                    <MyFilter
                      options={branches}
                      filter={setSelectedBranch_}
                      labelField={{
                        plural: "الفروع",
                        singular: "الفرع",
                        title: "الفرع ",
                      }}
                      isMulti={true}
                    />
                  </div>
                  <div>
                    <MyFilter
                      options={filteredUsers}
                      filter={setSelectedUsers_}
                      labelField={{
                        plural: "المستخدمين",
                        singular: "المستخدم",
                        title: "المستخدم ",
                      }}
                      isMulti={false}
                    />
                  </div>

                  <div>
                    {
                      <MyFilter
                        options={contracts}
                        filter={setSelectedContracts_}
                        labelField={{
                          plural: "العقود",
                          singular: "العقد",
                          title: "العقد",
                        }}
                        isMulti={false}
                      />
                    }
                  </div>
                  <div>
                    <MyFilter
                      options={
                        selectedContracts_?.length === 0
                          ? portfolios
                          : portfolios.filter(
                              (item) =>
                                item.contract_id === selectedContracts_[0]?.id
                            )
                      }
                      filter={setSelectedPortfolios_}
                      labelField={{
                        plural: "المحافظ",
                        singular: "المحفظة",
                        title: "المحفظة",
                      }}
                      isMulti={true}
                    />
                  </div>

                  <div>
                    {
                      <MyFilter
                        options={reachability}
                        filter={setSelectedReachability_}
                        labelField={{
                          plural: "حالات الوصول",
                          singular: "حالة الوصول",
                          title: "حالة الوصول",
                        }}
                        isMulti={false}
                      />
                    }
                  </div>
                  <div>
                    <MyFilter
                      options={filteredComment_status.filter(
                        (item) => item.contract_id === selectedContracts_[0]?.id
                      )}
                      filter={setSelectedCommentStatus_}
                      labelField={{
                        plural: "حالات العميل",
                        singular: "حالة",
                        title: "الحالة",
                      }}
                      isMulti={true}
                    />
                  </div>
                </div>
              </div>
              <div className="rounded-lg border border-gray-300 p-5 dark:border-night-border">
                <div className="grid grid-cols-2 gap-4">
                  {/*  <div className="col-span-2">
                    <FilterAmount
                      filter={setSelectedAmount_}
                      labelField={"المبلغ"}
                    />
                  </div> */}

                  <div>
                    <MyField
                      filter={setSelectedClientID_}
                      labelField={{
                        title: "معرف الحساب",
                      }}
                      type="text"
                    />
                  </div>
                  <div>
                    <MyField
                      filter={setSelectedAccountNumber_}
                      labelField={{
                        title: "رقم الحساب",
                      }}
                      type="number"
                    />
                  </div>
                </div>
              </div>
            </div>
            <hr className="my-6 h-px border-0 bg-gray-200 dark:bg-night-border" />
            <div className="divide-y divide-y-reverse ">
              <div className="mt-5 flex justify-end gap-2">
                <Button
                  variant="solid"
                  size="sm"
                  onClick={() => {
                    handelSearch();
                  }}
                >
                  بحث
                </Button>
                <Button
                  variant="default"
                  size="sm"
                  /*  onClick={() => {
                  setSelectedUsers_([]);
                  setSelectedAccountNumber_([]);
                  setSelectedCommentStatus_([]);
                  setSelectedResons_([]);
                  setSelectedArchiveBy_([]);
                  setSelectedContracts_([]);
                  setSelectedPortfolios_([]);
                  setSelectedReachability_([]);
                  fetchData();
                  setAdvanceSearch(false);
                }} */
                >
                  إلغاء
                </Button>
              </div>
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
};

export default PortfoliosTableFilter;
