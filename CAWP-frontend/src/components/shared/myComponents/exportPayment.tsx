import { useContext } from "react";
import ToastNotification from "@/components/shared/myComponents/ToastNotification";
import { useState, useEffect } from "react";
import Button from "@/components/ui/Button";
import { useTranslation } from "react-i18next";
import { HiPlusCircle, HiDownload } from "react-icons/hi";
import { apiGetpaymentsExport } from "@/services/CollectionsService";


function ExportByExcel({
  pramas,
  notificationFailedMessage,
  notificationTitle,
  notificationSuccessMessage,
}) {
  const { t } = useTranslation();
  const [isLoading, setIsLoading] = useState(false);

  const fetchData = async () => {
    await apiGetpaymentsExport({ filterData: pramas })
      .then(async (response) => {
        const ExcelJS = (await import("exceljs")).default;
        const { saveAs } = await import("file-saver");
        var wb = new ExcelJS.Workbook();
        var jsonData = response.data.payments;
        var data = jsonData.map((item) => {
          let approval_status = item?.approval_status;
          let approval_status_value =
            approval_status === null
              ? "تحت الاجراء"
              : approval_status === false
              ? "مرفوض"
              : "مقبول";

          return [
            item?.clint?.name,
            item?.clint?.identityNumber,
            item?.clint?.accountNumber,
            item?.clint?.portfolios?.contract.name,
            item?.amount,
            item?.collection_type?.name,
            item?.create_at ? new Date(item?.create_at) : null,
            item?.payment_date ? new Date(item?.payment_date) : null,
            item?.user?.name,
            item?.approval_at ? new Date(item?.approval_at) : null,
            item?.approval_by?.name,
            item?.approval_comment,
            item?.clint?.amount,
            item?.approval_status ? "مقبول" : "مرفوض",
          ];
        });

        // Add title row
        data.unshift(
          ["برنامج التحصيل الذكي | تحصيلات المستخدمين"],
          [
            "اسم العميل",
            "رقم الهوية",
            "رقم الحساب",
            "اسم العقد",
            "المبلغ",
            "نوع الدفع",
            "تاريخ الادخال",
            "تاريخ الدفع",
            "المستخدم",
            "تاريخ التعميد",
            "قام بالتعميد",
            "الملاحظات",
            "مبلغ المديونية",
            "الحالة",
          ]
        );
        var ws = wb.addWorksheet("clints");
        ws.views = [{ rightToLeft: true }];
        data.forEach((row, rowIndex) => {
          row.forEach((value, colIndex) => {
            let cell = ws.getCell(rowIndex + 1, colIndex + 1);
            cell.value = value;
            if (rowIndex === 0) {
              cell.alignment = { horizontal: "center" };
            }
          });
        });

        // Merge and center the first row
        ws.mergeCells(1, 1, 1, data[1].length);

        const buffer = await wb.xlsx.writeBuffer();
        const blob = new Blob([buffer], {
          type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        });
        saveAs(blob, "payments.xlsx");

        ToastNotification(
          t(notificationSuccessMessage),
          t(notificationTitle),
          "success"
        );
        setIsLoading(false);
      })
      .catch((error) => {
        console.log(error);
        setIsLoading(false);
        ToastNotification(
          t(notificationFailedMessage),
          t(notificationTitle),
          "danger"
        );
      });
  };

  useEffect(() => {
    //fetchData();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pramas]);

  const handlExportButton = () => {
    fetchData();
  };

  return (
    <div>
      <div>
        <Button
          variant="default"
          loading={isLoading}
          onClick={handlExportButton}
          size="sm"
          icon={<HiDownload />}
        >
          {t("download")}
        </Button>
      </div>
    </div>
  );
}

export default ExportByExcel;
