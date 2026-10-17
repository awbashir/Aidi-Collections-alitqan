import Select from "@/components/ui/Select";
import Badge from "@/components/ui/Badge";
import {
  setFilterData,
  setSearchClose,
  initialFilterData,
  useAppDispatch,
  useAppSelector,
} from "./store";
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
} from "@/services/ClientsService";
import MyFilter from "@/components/shared/myComponents/filter";
import SubStatusFilter from "@/components/shared/myComponents/subStatusFilter";
import MyField from "@/components/shared/myComponents/field";
import Button from "@/components/ui/Button";
import FilterAmount from "@/components/shared/myComponents/filterAmount";

type Option = {
  value: string;
  label: string;
  color: string;
};
import { useEffect, useRef, useState } from "react";
import {
  asSelection,
  selectionIds,
  selectionKey,
} from "@/utils/v2/listRestore";
import { mainStatusesFor } from "@/utils/commentStatusTree";

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

const ClientsTableFilter = () => {
  const dispatch = useAppDispatch();
  const search = useAppSelector((state) => state.clientsList.data.search);

  const { t } = useTranslation();

  /* The panel starts from the filters the list applies (kept in the store and
   * the URL hash), so after coming back from a case it shows them instead of
   * empty fields, and "search" keeps them. */
  const applied = useAppSelector((state) => state.clientsList.data.filterData);
  const [selectedContracts_, setSelectedContracts_] = useState(
    () => asSelection(applied?.customeFilterContractValue) ?? []
  );
  const [selectedPortfolios_, setSelectedPortfolios_] = useState(() =>
    asSelection(applied?.customeFilterPortfoliosValue)
  );
  const [selectedReachability_, setSelectedReachability_] = useState();
  const [selectedCommentStatus_, setSelectedCommentStatus_] = useState(() =>
    asSelection(applied?.customeFilterCommentStatusValue)
  );
  const [selectedCommentSubStatus_, setSelectedCommentSubStatus_] = useState<
    Array<{ id?: string }> | undefined
  >(() => asSelection(applied?.customeFilterCommentSubStatusValue));
  const [selectedUsers_, setSelectedUsers_] = useState(() =>
    asSelection(applied?.customeFilterUserValue)
  );
  const [selectedAccountNumber_, setSelectedAccountNumber_] = useState();
  const [selectedArchiveBy_, setSelectedArchiveBy_] = useState();
  const [selectedResons_, setSelectedResons_] = useState();
  const [selectedBranches_, setSelectedBranches_] = useState(() =>
    asSelection(applied?.customeFilterBranchesValue)
  );
  const [selectedAttribute_, setSelectedAttribute_] = useState(
    () => applied?.customeFilterAttributeValue
  );
  const [selectedAmount_, setSelectedAmount_] = useState();
  const [selectedPortfolioCreateAtFrom_, setSelectedPortfolioCreateAtFrom_] =
    useState(() => applied?.customeFilterPortfolioCreateAtFromValue);
  const [selectedPortfolioCreateAtTo_, setSelectedPortfolioCreateAtTo_] =
    useState(() => applied?.customeFilterPortfolioCreateAtToValue);

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

  const [loadingUsers, setLoadingUsers] = useState(false);
  const fetchUsers = async () => {
    setLoadingUsers(true);
    const response = await apiGetUsers();
    setUsers(response.data.users);
    setLoadingUsers(false);
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
  /* Clear the statuses when the reachability or contract selection really
   * changes (not when a select re-reports the same ids, or on mount, which
   * would wipe the statuses restored above). */
  const statusScope = `${selectionKey(selectedReachability_)}|${selectionKey(
    selectedContracts_
  )}`;
  const lastStatusScope = useRef(statusScope);
  useEffect(() => {
    if (lastStatusScope.current !== statusScope) {
      lastStatusScope.current = statusScope;
      setSelectedCommentStatus_([]);
      setSelectedCommentSubStatus_([]);
    }
    if (selectedReachability_?.length > 0) {
      const commentstatus_ = comment_status.filter((status) =>
        selectedReachability_.some(
          (reachability) => reachability.id === status.CommentReachability_id,
        ),
      );
      setFilteredComment_status(commentstatus_);
    } else {
      setFilteredComment_status(comment_status);
    }

    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [statusScope, comment_status]);

  const [filiteredUsers, setFiliteredUsers] = useState([]);
  const usersScope = selectionKey(selectedBranches_);
  const lastUsersScope = useRef(usersScope);
  useEffect(() => {
    if (lastUsersScope.current !== usersScope) {
      lastUsersScope.current = usersScope;
      setSelectedUsers_([]);
    }
    if (selectedBranches_ && selectedBranches_.length > 0) {
      const users_ = users.filter(
        (user) =>
          selectedBranches_.some((branch) => branch.id === user.branch_id) &&
          user.is_active === true,
      );
      setFiliteredUsers(users_);
    } else {
      setFiliteredUsers(users.filter((user) => user.is_active === true));
    }

    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [usersScope, users]);

  const filterData = useAppSelector(
    (state) => state.clientsList.data.filterData,
  );
  const handelSearch = () => {
    dispatch(
      setFilterData({
        ...filterData,

        //-----------------
        customeFilterBranchesValue: selectedBranches_?.map(
          (branch) => branch.id,
        ),
        customeFilterAttributeValue: selectedAttribute_,

        customeFilterContractValue: selectedContracts_?.map(
          (contract) => contract.id,
        ),

        customeFilterUserValue: selectedUsers_?.map((user) => user.id),

        customeFilterPortfoliosValue: selectedPortfolios_?.map(
          (portfolio) => portfolio.id,
        ),

        customeFilterCommentStatusValue: selectedCommentStatus_?.map(
          (status) => status.id,
        ),
        customeFilterCommentSubStatusValue: selectedCommentSubStatus_?.map(
          (status) => status.id,
        ),

        //amount
        customeFilterAmountValue: selectedAmount_,
        customeFilterPortfolioCreateAtFromValue: selectedPortfolioCreateAtFrom_,
        customeFilterPortfolioCreateAtToValue: selectedPortfolioCreateAtTo_,
      }),
    );
  };

  const handleCancel = () => {
    setSelectedContracts_([]);
    setSelectedPortfolios_(undefined);
    setSelectedReachability_(undefined);
    setSelectedCommentStatus_(undefined);
    setSelectedCommentSubStatus_(undefined);
    setSelectedUsers_(undefined);
    setSelectedAccountNumber_(undefined);
    setSelectedArchiveBy_(undefined);
    setSelectedResons_(undefined);
    setSelectedBranches_(undefined);
    setSelectedAttribute_(undefined);
    setSelectedAmount_(undefined);
    setSelectedPortfolioCreateAtFrom_(undefined);
    setSelectedPortfolioCreateAtTo_(undefined);
    dispatch(setFilterData(initialFilterData));
    dispatch(setSearchClose());
  };

  return (
    <>
      {search ? (
        <div>
          <div className="rounded-t-lg border border-gray-300 dark:border-night-border bg-slate-150 dark:bg-night-raised px-5 py-3 text-[13.5px] font-extrabold text-navy-600 dark:text-night-strong">
            بحث العملاء
          </div>

          <div className="rounded-b-lg border border-t-0 border-gray-300 dark:border-night-border p-5">
            <div className="grid grid-cols-2 gap-4">
              <div className="rounded-lg border border-gray-300 dark:border-night-border p-5 pb-8">
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    {" "}
                    <MyFilter
                      options={contracts}
                      filter={setSelectedContracts_}
                      selectedIds={selectionIds(selectedContracts_)}
                      labelField={{
                        plural: "العقود",
                        singular: "العقد",
                        title: "العقود ",
                      }}
                      isMulti={false}
                    />
                  </div>
                  <div>
                    <MyFilter
                      options={
                        selectedContracts_?.length === 0
                          ? portfolios
                          : portfolios.filter(
                              (item) =>
                                item.contract_id === selectedContracts_[0]?.id,
                            )
                      }
                      filter={setSelectedPortfolios_}
                      selectedIds={selectionIds(selectedPortfolios_)}
                      labelField={{
                        plural: "المحافظ",
                        singular: "المحفظة",
                        title: "المحافظ",
                      }}
                      isMulti={false}
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
                      options={mainStatusesFor(filteredComment_status, selectedContracts_[0]?.id)}
                      filter={setSelectedCommentStatus_}
                      selectedIds={selectionIds(selectedCommentStatus_)}
                      labelField={{
                        plural: "حالات العميل",
                        singular: "حالة",
                        title: "الحالة",
                      }}
                      isMulti={true}
                    />
                  </div>
                  <div>
                    <SubStatusFilter
                      statuses={comment_status}
                      selectedMain={selectedCommentStatus_}
                      filter={setSelectedCommentSubStatus_}
                      selectedIds={selectionIds(
                        selectedCommentSubStatus_
                      ) as string[] | undefined}
                    />
                  </div>
                </div>
              </div>
              <div className="rounded-lg border border-gray-300 dark:border-night-border p-5 pb-8">
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <MyFilter
                      options={branches}
                      filter={setSelectedBranches_}
                      selectedIds={selectionIds(selectedBranches_)}
                      labelField={{
                        plural: "الفروع",
                        singular: "الفرع",
                        title: "فرع المستخدم",
                      }}
                      isMulti={true}
                    />
                  </div>

                  <div>
                    <MyFilter
                      options={filiteredUsers}
                      filter={setSelectedUsers_}
                      selectedIds={selectionIds(selectedUsers_)}
                      labelField={{
                        plural: "المستخدمين",
                        singular: "مستخدم",
                        title: "اسم المستخدم",
                      }}
                      isMulti={true}
                      loading={loadingUsers}
                    />
                  </div>
                  {/*   <div>
                    <MyField
                      filter={setSelectedAccountNumber_}
                      labelField={{
                        title: "رقم الحساب",
                      }}
                      type="number"
                    />
                  </div> */}

                  <div>
                    <MyField
                      filter={setSelectedAttribute_}
                      initialValue={selectedAttribute_}
                      labelField={{
                        title: "بحث",
                      }}
                      type="text"
                    />
                  </div>

                  <div className="col-span-2">
                    <div className="grid grid-cols-2 gap-4">
                      <div>
                        <MyField
                          filter={setSelectedPortfolioCreateAtFrom_}
                          initialValue={selectedPortfolioCreateAtFrom_}
                          labelField={{
                            title: "تاريخ المحفظة من",
                          }}
                          type="date"
                        />
                      </div>
                      <div>
                        <MyField
                          filter={setSelectedPortfolioCreateAtTo_}
                          initialValue={selectedPortfolioCreateAtTo_}
                          labelField={{
                            title: "الى",
                          }}
                          type="date"
                        />
                      </div>
                    </div>
                  </div>
                  {/*         <div className="col-span-2">
                    <FilterAmount
                      filter={setSelectedAmount_}
                      labelField={"المبلغ"}
                    />
                  </div>

                  <div>
                    <MyFilter
                      options={users}
                      filter={setSelectedArchiveBy_}
                      labelField={{
                        plural: "مستخدمين",
                        singular: "مستخدم",
                        title: "قام بنقل",
                      }}
                      isMulti={true}
                    />
                  </div>

                  <div>
                    <MyFilter
                      options={archive_reason}
                      filter={setSelectedResons_}
                      labelField={{
                        plural: "اسباب النقل",
                        singular: "سبب النقل",
                        title: "سبب النقل",
                      }}
                      isMulti={true}
                    />
                  </div> */}
                </div>
              </div>
            </div>
            <hr className="my-6 h-px border-0 bg-gray-200 dark:bg-night-border" />
            <div className="divide-y divide-y-reverse ">
              <div className="flex gap-2  justify-end mt-5 ">
                <Button
                  variant="solid"
                  size="sm"
                  onClick={() => {
                    handelSearch();
                  }}
                >
                  بحث
                </Button>
                <Button variant="default" size="sm" onClick={handleCancel}>
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

export default ClientsTableFilter;
