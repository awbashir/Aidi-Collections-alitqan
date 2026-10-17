import Select from "@/components/ui/Select";
import Badge from "@/components/ui/Badge";
import {
  setFilterData,
  useAppDispatch,
  useAppSelector,
  setSearchClose,
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
} from "@/services/ArchiveService";
import MyFilter from "@/components/shared/myComponents/filter";
import SubStatusFilter from "@/components/shared/myComponents/subStatusFilter";
import MyField from "@/components/shared/myComponents/field";
import Button from "@/components/ui/Button";

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

const PortfoliosTableFilter = () => {
  const dispatch = useAppDispatch();
  const search = useAppSelector((state) => state.archiveList.data.search);

  const { t } = useTranslation();
  /* The panel starts from the filters the list applies (kept in the store and
   * the URL hash), so after coming back from a case it shows them. */
  const applied = useAppSelector((state) => state.archiveList.data.filterData);
  const [selectedContracts_, setSelectedContracts_] = useState(
    () => asSelection(applied?.selectedContracts_Qkey) ?? []
  );
  const [selectedPortfolios_, setSelectedPortfolios_] = useState(() =>
    asSelection(applied?.SelectedPortfolios_Qkey)
  );
  const [selectedReachability_, setSelectedReachability_] = useState();
  const [selectedCommentStatus_, setSelectedCommentStatus_] = useState(() =>
    asSelection(applied?.SelectedCommentStatus_Qkey)
  );
  const [selectedCommentSubStatus_, setSelectedCommentSubStatus_] = useState<
    Array<{ id?: string }> | undefined
  >(() => asSelection(applied?.SelectedCommentSubStatus_Qkey));
  const [selectedUsers_, setSelectedUsers_] = useState(() =>
    asSelection(applied?.selectedUsers_Qkey)
  );
  const [selectedAccountNumber_, setSelectedAccountNumber_] = useState(
    () => applied?.selectedAccountNumber_Qkey
  );
  const [selectedArchiveBy_, setSelectedArchiveBy_] = useState(() =>
    asSelection(applied?.selectedArchiveBy_Qkey)
  );
  const [selectedResons_, setSelectedResons_] = useState(() =>
    asSelection(applied?.SelectedResons_Qkey)
  );

  const [contracts, setContracts] = useState<Contract[]>([]);
  const [portfolios, setPortfolios] = useState<Portfolio[]>([]);
  const [reachability, setReachability] = useState<Reachability[]>([]);
  const [comment_status, setComment_status] = useState<CommentStatus[]>([]);
  const [users, setUsers] = useState<User[]>([]);
  const [archive_reason, setArchive_reason] = useState<ArchiveReason[]>([]);

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

  const [filteredComment_status, setFilteredComment_status] = useState([]);
  /* Clear the statuses only when the reachability or contract selection
   * really changes (not on mount, which would wipe the restored ones). */
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
          (reachability) => reachability.id === status.CommentReachability_id
        )
      );
      setFilteredComment_status(commentstatus_);
    } else {
      setFilteredComment_status(comment_status);
    }

    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [statusScope, comment_status]);

  const filterData = useAppSelector(
    (state) => state.archiveList.data.filterData
  );

  const handelSearch = () => {
    dispatch(
      setFilterData({
        ...filterData,

        //-----------------

        selectedAccountNumber_Qkey: selectedAccountNumber_,

        //-----------------------
        selectedContracts_Qkey: selectedContracts_?.map(
          (contract) => contract.id
        ),
        //----------------------

        selectedUsers_Qkey: selectedUsers_?.map((user) => user.id),
        //-----------------

        SelectedPortfolios_Qkey: selectedPortfolios_?.map(
          (portfolio) => portfolio.id
        ),
        //----------------------
        SelectedCommentStatus_Qkey: selectedCommentStatus_?.map(
          (status) => status.id
        ),
        SelectedCommentSubStatus_Qkey: selectedCommentSubStatus_?.map(
          (status) => status.id
        ),

        //-------------------

        SelectedResons_Qkey: selectedResons_?.map((status) => status.id),
        //-------------------

        selectedArchiveBy_Qkey: selectedArchiveBy_?.map((status) => status.id),
      })
    );
  };

  const [isResetting, setIsResetting] = useState(false);

  const handleReset = () => {
    setIsResetting(true); // Set the reset flag
    setSelectedUsers_([]);
    setSelectedAccountNumber_([]);
    setSelectedCommentStatus_([]);
    setSelectedCommentSubStatus_([]);
    setSelectedResons_([]);
    setSelectedArchiveBy_([]);
    setSelectedContracts_([]);
    setSelectedPortfolios_([]);
    setSelectedReachability_([]);
    dispatch(setFilterData({}));
    dispatch(setSearchClose());
  };

  // Trigger handelSearch after reset is complete
  useEffect(() => {
    if (isResetting) {
      handelSearch(); // Call the search handler
      setIsResetting(false); // Reset the flag
    }
  }, [isResetting]);

  return (
    <>
      {search ? (
        <div>
          <div className="rounded-t-lg border border-gray-300 bg-gray-100 px-5 py-3 text-[15px] font-extrabold text-navy-600 dark:border-night-border dark:bg-night-raised dark:text-night-body">
            {t("archive.searchclientsArchive")}
          </div>

          <div className="rounded-b-lg border border-t-0 border-gray-300 bg-white p-5 dark:border-night-border dark:bg-night-card">
            <div className="grid grid-cols-2 gap-4">
              <div className="rounded-lg border border-gray-300 p-5 dark:border-night-border">
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    {" "}
                    <MyFilter
                      options={contracts || []}
                      filter={setSelectedContracts_}
                      selectedIds={selectionIds(selectedContracts_)}
                      labelField={{
                        plural: "nav.contracts.contracts",
                        singular: "nav.contracts.singular",
                        title: "nav.contracts.contracts",
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
                                item?.contracts?.id ===
                                selectedContracts_[0]?.id
                            )
                      }
                      filter={setSelectedPortfolios_}
                      selectedIds={selectionIds(selectedPortfolios_)}
                      labelField={{
                        plural: "nav.portfolio.portfolio",
                        singular: "nav.portfolio.singular",
                        title: "nav.portfolio.portfolio",
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
                          plural: "reachability",
                          singular: "reachability",
                          title: "reachability",
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
                        plural: "Clientstatus",
                        singular: "status",
                        title: "Clientstatus",
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
              <div className="rounded-lg border border-gray-300 p-5 dark:border-night-border">
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <MyFilter
                      options={users}
                      filter={setSelectedUsers_}
                      selectedIds={selectionIds(selectedUsers_)}
                      labelField={{
                        plural: "users.title",
                        singular: "users.user",
                        title: "users.name",
                      }}
                      isMulti={true}
                    />
                  </div>
                  <div>
                    <MyField
                      filter={setSelectedAccountNumber_}
                      initialValue={selectedAccountNumber_}
                      labelField={{
                        title: "clients.accountNumber",
                      }}
                      type="text"
                    />
                  </div>

                  <div>
                    <MyFilter
                      options={users}
                      filter={setSelectedArchiveBy_}
                      selectedIds={selectionIds(selectedArchiveBy_)}
                      labelField={{
                        plural: "users.title",
                        singular: "users.user",
                        title: "ArchiveBy",
                      }}
                      isMulti={true}
                    />
                  </div>

                  <div>
                    <MyFilter
                      options={archive_reason}
                      filter={setSelectedResons_}
                      selectedIds={selectionIds(selectedResons_)}
                      labelField={{
                        plural: "ArchiveReasons",
                        singular: "ArchiveReason",
                        title: "ArchiveReasons",
                      }}
                      isMulti={true}
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
                  {t("search")}
                </Button>
                <Button variant="default" size="sm" onClick={handleReset}>
                  {t("cancel")}
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
