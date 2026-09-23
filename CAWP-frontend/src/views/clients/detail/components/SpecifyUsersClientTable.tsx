import Table from "@/components/ui/Table";
import { useTranslation } from "react-i18next";
import Card from "@/components/ui/Card";
import { getSpecifyUsers, useAppDispatch, useAppSelector } from "../store";
import { useEffect } from "react";
import { NumericFormat } from "react-number-format";

const { Tr, Th, Td, THead, TBody } = Table;

const SpecifyUsersClient = () => {
  const { t } = useTranslation();

  const dispatch = useAppDispatch();
  const client = useAppSelector(
    (state) => state.crmCustomerDetails?.data?.clientData
  );

  const SpecifyUsers = useAppSelector(
    (state) => state.crmCustomerDetails?.data?.SpecifyUsers
  );

  useEffect(() => {
    dispatch(getSpecifyUsers(client?.id));
  }, [client]);

  return (
    <div>
      <div className="mt-8">
        <Table compact>
          <THead>
            <Tr>
              <Th>{t("users.name")}</Th>
              <Th>{t("usersArchive.date")}</Th>
              <Th>{t("usersArchive.dateCancel")}</Th>
              <Th>{t("usersArchive.archiveBy")}</Th>
            </Tr>
          </THead>
          <TBody>
            {SpecifyUsers?.map((user, i) => (
              <Tr key={i}>
                <Td>{user?.user?.frist_name + " " + user?.user?.last_name}</Td>
                <Td className="tabular-nums">
                  {new Date(user?.create_at).toLocaleDateString("en-CA")}
                </Td>
                <Td className="tabular-nums">
                  {user?.create_at2
                    ? new Date(user.create_at2).toLocaleDateString("en-CA")
                    : ""}
                </Td>
                <Td>{user?.create_by?.name}</Td>
              </Tr>
            ))}
          </TBody>
        </Table>
      </div>
    </div>
  );
};

export default SpecifyUsersClient;
