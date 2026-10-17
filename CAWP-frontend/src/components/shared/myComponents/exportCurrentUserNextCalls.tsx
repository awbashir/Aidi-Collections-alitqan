import { useContext } from "react";
import ToastNotification from "@/components/shared/myComponents/ToastNotification";
import { useState, useEffect } from "react";
import Button from "@/components/ui/Button";
import { useTranslation } from "react-i18next";
import { HiPlusCircle, HiDownload } from "react-icons/hi";
import { apiGetCurrentUserNextCallsExport } from "@/services/ReportsService";


function ExportByExcel({
  pramas,
  notificationFailedMessage,
  notificationTitle,
  notificationSuccessMessage,
}) {
  const { t } = useTranslation();
  const [isLoading, setIsLoading] = useState(false);

  const fetchData = async () => {
    await apiGetCurrentUserNextCallsExport({ filterData: pramas })
      .then(async (response) => {
        const ExcelJS = (await import("exceljs")).default;
        const { saveAs } = await import("file-saver");
        var wb = new ExcelJS.Workbook();
        var jsonData = response.data.clints;
        var data = jsonData.map((item) => {
          var days = new Date(item?.specifyUser?.create_at);
          var days2 = new Date();
          var timeDiff = Math.abs(days2.getTime() - days.getTime());
          var diffDays = Math.ceil(timeDiff / (1000 * 3600 * 24));
          return [
            item?.portfolios?.name,
            item?.contract?.name,
            item?.user?.name,
            diffDays ? diffDays : 0,
            item?.comment_status?.name,
            item?.comment_sub_status?.name || "",
            item?.accountNumber,
            item?.amount,
            item?.name,
            item?.identityNumber,
            new Date(item?.comment?.next_contact_date),
          ];
        });

        // Add title row
        data.unshift(
          ["برنامج التحصيل الذكي | المتابعات"],
          [
            "المحفظة",
            "اسم العقد",
            "المستخدم",
            "ايام الاسناد",
            "حالة العميل",
            "الحالة الفرعية",
            "رقم الحساب",
            "مبلغ المديونية",
            "اسم العميل",
            "رقم الهوية",
            "تاريخ الاتصال القادم",
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
        saveAs(blob, "nextCalls.xlsx");

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
