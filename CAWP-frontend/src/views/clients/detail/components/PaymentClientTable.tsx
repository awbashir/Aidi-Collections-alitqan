import Table from "@/components/ui/Table";
import { useTranslation } from "react-i18next";
import Card from "@/components/ui/Card";
import { getPayments, useAppDispatch, useAppSelector } from "../store";
import { useEffect, useState } from "react";
import { NumericFormat } from "react-number-format";
import Loading from "@/components/shared/Loading";
import StatusPill from "@/components/shared/StatusPill";
import { HiOutlinePaperClip } from "react-icons/hi";
import { useV2Query } from "@/utils/v2/hooks";
import { paymentAttachmentsApi } from "@/services/v2/payAttApi";
import type { PaymentAttachment } from "@/services/v2";
import {
  PaymentReceiptsDialog,
  ReceiptLinks,
  useCanAttachReceipts,
} from "@/views/paymentAttachments";

const { Tr, Th, Td, THead, TBody } = Table;

/** The fields of a legacy GET /payments/clint/<id> row used by the receipt column. */
type LegacyPaymentRow = { id: string; approval_status: boolean | null; is_deleted: boolean };

/** Receipt column: view links, and "attach" for a row without one or a pending row. */
const ReceiptCell = ({
  row,
  files,
  canAttach,
  onManage,
}: {
  row: LegacyPaymentRow;
  files: PaymentAttachment[];
  canAttach: boolean;
  onManage: (paymentId: string) => void;
}) => {
  const { t } = useTranslation();
  const pending = row?.approval_status == null && !row?.is_deleted;
  const attachable = canAttach && !row?.is_deleted && (files.length === 0 || pending);
  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
      <ReceiptLinks attachments={files} compact />
      {attachable && (
        <button
          type="button"
          className="inline-flex items-center gap-1 text-[13px] font-semibold text-gray-600 hover:text-azure-600"
          onClick={() => onManage(String(row.id))}
          data-testid="payatt-manage"
        >
          {files.length === 0 && <HiOutlinePaperClip />}
          {files.length === 0 ? t("p7.payAtt.attach") : t("p7.payAtt.manage")}
        </button>
      )}
      {files.length === 0 && !attachable && <span className="text-gray-400">—</span>}
    </div>
  );
};

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

  // Receipts of this case's payments (v2, one call); reloaded with the payments list.
  // Without a case-money key or outside the scope the column is simply not shown.
  const canAttach = useCanAttachReceipts();
  const [receiptsTick, setReceiptsTick] = useState(0);
  const [receiptsFor, setReceiptsFor] = useState<string | null>(null);
  const receipts = useV2Query(
    client?.id
      ? (signal) => paymentAttachmentsApi.forCase(String(client.id), signal)
      : null,
    [client?.id, payments, receiptsTick]
  );
  const showReceipts = !!receipts.data;
  const caseFlags = client as { is_archive?: boolean; is_locked?: boolean } | undefined;
  const caseClosed = !!(caseFlags?.is_archive || caseFlags?.is_locked);

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
                {showReceipts && <Th>{t("p7.payAtt.column")}</Th>}
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
                  {showReceipts && (
                    <Td>
                      <ReceiptCell
                        row={payment as unknown as LegacyPaymentRow}
                        files={
                          receipts.data?.payments?.[
                            String((payment as unknown as LegacyPaymentRow)?.id)
                          ] || []
                        }
                        canAttach={canAttach && !caseClosed}
                        onManage={setReceiptsFor}
                      />
                    </Td>
                  )}
                </Tr>
              ))}
            </TBody>
          </Table>
        </Loading>
        <PaymentReceiptsDialog
          paymentId={receiptsFor}
          isOpen={!!receiptsFor}
          canAttach={canAttach}
          locked={caseClosed}
          onClose={() => setReceiptsFor(null)}
          onChanged={() => setReceiptsTick((n) => n + 1)}
        />
      </div>
    </div>
  );
};

export default PaymentClient;
