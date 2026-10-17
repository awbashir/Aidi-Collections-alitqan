import { useContext } from "react";
import ToastNotification from "@/components/shared/myComponents/ToastNotification";
import { useState, useEffect } from "react";
import Button from "@/components/ui/Button";
import { useTranslation } from "react-i18next";
import { HiPlusCircle, HiDownload } from "react-icons/hi";
import { apiGetCurrentUserPaymentDatesExport } from "@/services/ReportsService";


function ExportByExcel({
  pramas,
  notificationFailedMessage,
  notificationTitle,
  notificationSuccessMessage,
}) {
  const { t } = useTranslation();
  const [isLoading, setIsLoading] = useState(false);

  const fetchData = async () => {
    await apiGetCurrentUserPaymentDatesExport({ filterData: pramas })
      .then(async (response) => {
        const ExcelJS = (await import("exceljs")).default;
        const { saveAs } = await import("file-saver");
        var wb = new ExcelJS.Workbook();
        var jsonData = response.data.payment_dates;
        var data = jsonData.map((item) => {
          let approval_status = item?.approval_status;
          let approval_status_value =
            approval_status === null
              ? "تحت الاجراء"
              : approval_status === false
              ? "مرفوض"
              : "مقبول";

          return [
            item?.clint?.accountNumber,
            item?.clint?.name,
            item?.clint?.identityNumber,

            item?.portfolios?.contract.name,
            item?.comment_status?.name,
            item?.comment_sub_status?.name || "",
            item?.clint?.amount,
            item?.amount,
            new Date(item?.payment_date),
            item?.user?.name,
          ];
        });

        // Add title row
        data.unshift(
          ["برنامج التحصيل الذكي | تحصيلات المستخدمين"],
          [
            "رقم الحساب",
            "اسم العميل",
            "رقم الهوية",
            "اسم العقد",
            "حالة العميل",
            "الحالة الفرعية",
            "مبلغ المديونية",
            "مبلغ السداد",
            "موعد السداد",
            "المستخدم",
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
        saveAs(blob, "paymentsDates.xlsx");

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
