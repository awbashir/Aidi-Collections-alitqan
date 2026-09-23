import Table from "@/components/ui/Table";
import { useTranslation } from "react-i18next";
import Card from "@/components/ui/Card";
import { getPayments, useAppDispatch, useAppSelector } from "../store";
import { useEffect } from "react";
import { NumericFormat } from "react-number-format";
import Loading from "@/components/shared/Loading";
import StatusPill from "@/components/shared/StatusPill";

const { Tr, Th, Td, THead, TBody } = Table;

const PaymentClient = () => {
  const { t } = useTranslation();

  const dispatch = useAppDispatch();
  const client = useAppSelector(
    (state) => state.crmCustomerDetails?.data?.clientData
  );
  const payments = useAppSelector(
    (state) => state.crmCustomerDetails?.data?.payments
  );

  const loadingPayment = useAppSelector(
    (state) => state.crmCustomerDetails?.data?.loadingPayment
  );

  useEffect(() => {
    dispatch(getPayments(client?.id));
  }, [client]);

  return (
    <div>
      <div className="mt-8 min-h-[300px]">
        <Loading loading={loadingPayment} type="cover">
          <Table compact>
            <THead>
              <Tr>
                <Th>{t("payments.date")}</Th>
                <Th className="text-end">{t("payments.amount")}</Th>
                <Th>{t("payments.method")}</Th>
                <Th>{t("payments.type")}</Th>
                <Th>{t("users.name")}</Th>
                <Th>{t("status")}</Th>
              </Tr>
            </THead>
            <TBody>
              {payments?.map((payment, i) => (
                <Tr key={i}>
                  <Td className="tabular-nums">
                    {new Date(payment?.payment_date).toLocaleDateString(
                      "en-CA"
                    )}
                  </Td>
                  <Td className="text-end font-extrabold tabular-nums !text-navy-600">
                    <NumericFormat
                      value={payment?.amount ? payment?.amount : 0}
                      displayType={"text"}
                      thousandSeparator={true}
                      decimalScale={2}
                      fixedDecimalScale={true}
                    />
                  </Td>
                  <Td>{payment?.collection_method?.name}</Td>
                  <Td>{payment?.collection_type?.name}</Td>
                  <Td>
                    {payment?.user?.frist_name + " " + payment?.user?.last_name}
                  </Td>
                  <Td>
                    {payment?.approval_status === true ? (
                      <StatusPill tone="success">تمت الموافقة</StatusPill>
                    ) : payment?.approval_status === false ? (
                      <StatusPill tone="danger">تم الرفض</StatusPill>
                    ) : (
                      <StatusPill tone="warning">قيد الانتظار</StatusPill>
                    )}
                  </Td>
                </Tr>
              ))}
            </TBody>
          </Table>
        </Loading>
      </div>
    </div>
  );
};

export default PaymentClient;
