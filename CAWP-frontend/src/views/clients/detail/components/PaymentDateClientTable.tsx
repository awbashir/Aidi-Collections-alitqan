import Table from "@/components/ui/Table";
import { useTranslation } from "react-i18next";
import Card from "@/components/ui/Card";
import { getPaymentDate, useAppDispatch, useAppSelector } from "../store";
import { useEffect } from "react";
import { NumericFormat } from "react-number-format";
import Loading from "@/components/shared/Loading";

const { Tr, Th, Td, THead, TBody } = Table;

const PaymentDateClient = () => {
  const { t } = useTranslation();

  const dispatch = useAppDispatch();
  const client = useAppSelector(
    (state) => state.crmCustomerDetails?.data?.clientData
  );

  const loadingPaymentDate = useAppSelector(
    (state) => state.crmCustomerDetails?.data?.loadingPaymentDate
  );

  const payment_date = useAppSelector(
    (state) => state.crmCustomerDetails?.data?.paymentDate
  );

  useEffect(() => {
    dispatch(getPaymentDate(client?.id));
  }, [client]);

  return (
    <div>
      <div className="mt-8 min-h-[300px]">
        <Loading loading={loadingPaymentDate} type="cover">
          <Table compact>
            <THead>
              <Tr>
                <Th>{t("payments.date")}</Th>
                <Th className="text-end">{t("paymentsDate.amount")}</Th>

                <Th>{t("users.name")}</Th>
              </Tr>
            </THead>
            <TBody>
              {payment_date?.map((payment, i) => (
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

                  <Td>
                    {payment?.user?.frist_name + " " + payment?.user?.last_name}
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

export default PaymentDateClient;
