import { useContext } from "react";
import ToastNotification from "@/components/shared/myComponents/ToastNotification";
import { useState, useEffect } from "react";
import Button from "@/components/ui/Button";
import { useTranslation } from "react-i18next";
import { HiPlusCircle, HiDownload } from "react-icons/hi";
import { apiGetCommentsExport } from "@/services/ReportsService";


function getLatestPerClient(rows) {
  const latestByClient = new Map();
  (rows || []).forEach((row) => {
    const key = row?.clint_id ?? row;
    const rowTime = new Date(row?.create_at).getTime();
    const isRowTimeValid = !Number.isNaN(rowTime);
    const existing = latestByClient.get(key);
    if (!existing) {
      latestByClient.set(key, row);
      return;
    }
    const existingTime = new Date(existing?.create_at).getTime();
    const isExistingTimeValid = !Number.isNaN(existingTime);
    if (isRowTimeValid && (!isExistingTimeValid || rowTime > existingTime)) {
      latestByClient.set(key, row);
    }
  });
  return Array.from(latestByClient.values());
}

function ExportByExcel({
  pramas,
  latestOnly,
  notificationFailedMessage,
  notificationTitle,
  notificationSuccessMessage,
}) {
  const { t } = useTranslation();
  const [isLoading, setIsLoading] = useState(false);

  const fetchData = async () => {
    setIsLoading(true);
    await apiGetCommentsExport({ filterData: pramas })
      .then(async (response) => {
        const ExcelJS = (await import("exceljs")).default;
        const { saveAs } = await import("file-saver");
        var wb = new ExcelJS.Workbook();
        var jsonData = latestOnly
          ? getLatestPerClient(response.data.comments)
          : response.data.comments;
        var data = jsonData.map((item) => {
          return [
            item?.portfolios?.name,
            item?.portfolios?.contract?.name,
            item?.user?.name,
            new Date(item?.create_at).toLocaleString("en-SA"),
            new Date(item?.create_at).toLocaleTimeString("en-SA"),
            new Date(item?.create_at).getHours(),
            item?.reachability?.name,
            item?.status?.name,
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
            "وقت المتابعة",
            "الساعات",
            "الوصول",
            "الحالة",
            "التعليق",
            "طريقة الاتصال",
            "رقم الحساب",
            "المبلغ",
            "اسم العميل",
            "رقم الهوية",
            "رقم الجوال",
            "رقم الجوال",
          ]
        );
        var ws = wb.addWorksheet("المتابعات");
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
        saveAs(blob, "متابعات العملاء.xlsx");

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
