import { useContext } from "react";
import ToastNotification from "@/components/shared/myComponents/ToastNotification";
import { useState, useEffect } from "react";
import Button from "@/components/ui/Button";
import { useTranslation } from "react-i18next";
import { HiPlusCircle, HiDownload } from "react-icons/hi";
import { apiGetCurrentUserCommentsExport } from "@/services/ReportsService";


function ExportByExcel({
  pramas,
  notificationFailedMessage,
  notificationTitle,
  notificationSuccessMessage,
}) {
  const { t } = useTranslation();
  const [isLoading, setIsLoading] = useState(false);

  const fetchData = async () => {
    await apiGetCurrentUserCommentsExport({ filterData: pramas })
      .then(async (response) => {
        const ExcelJS = (await import("exceljs")).default;
        const { saveAs } = await import("file-saver");
        var wb = new ExcelJS.Workbook();
        var jsonData = response.data.comments;
        var data = jsonData.map((item) => {
          return [
            item?.portfolios?.name,
            item?.portfolios?.contract?.name,
            item?.user?.name,
            new Date(item?.create_at),
            item?.reachability?.name,
            item?.status?.name,
            item?.sub_status?.name || "",
            item?.comment,
            item?.contact_method?.name === "جوال" ||
            item?.contact_method?.name === "تحويلة"
              ? "اتصال"
              : item?.contact_method?.name,
            item?.clint?.accountNumber,
            item?.clint?.amount,
            item?.clint?.name,
            item?.clint?.identityNumber,
            item?.contactNumber || "",
            new Date(item?.clint?.attribute?.BATCH_DATE),
            item?.clint?.attribute?.email || "",
            item?.clint?.attribute?.mobile_1 ||
              item?.clint?.attribute?.mobile_2 ||
              item?.clint?.attribute?.mobile_3 ||
              item?.clint?.attribute?.MSISDN ||
              "",
          ];
        });

        // Add title row
        data.unshift(
          ["برنامج التحصيل الذكي | المتابعات"],
          [
            " المحفظة",
            " العقد",
            "المستخدم",
            "تاريخ المتابعة",
            "الوصول",
            "الحالة",
            "الحالة الفرعية",
            "التعليق",
            "طريقة الاتصال",
            "رقم الحساب",
            "المبلغ",
            "اسم العميل",
            "رقم الهوية",
            "رقم الجوال",
            "تاريخ الدفع",
            "البريد الالكتروني",
            "رقم الجوال",
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
