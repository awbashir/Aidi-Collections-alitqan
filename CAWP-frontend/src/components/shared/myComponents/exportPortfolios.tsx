import { useContext } from "react";
import ToastNotification from "@/components/shared/myComponents/ToastNotification";
import { useState, useEffect } from "react";
import Button from "@/components/ui/Button";
import { useTranslation } from "react-i18next";
import { HiPlusCircle, HiDownload } from "react-icons/hi";
import { apiGetPortfoliosExport } from "@/services/ReportsService";


function ExportByExcel({
  pramas,
  notificationFailedMessage,
  notificationTitle,
  notificationSuccessMessage,
}) {
  const { t } = useTranslation();
  const [isLoading, setIsLoading] = useState(false);

  const fetchData = async () => {
    await apiGetPortfoliosExport({ filterData: pramas })
      .then(async (response) => {
        const ExcelJS = (await import("exceljs")).default;
        const { saveAs } = await import("file-saver");
        var wb = new ExcelJS.Workbook();
        var jsonData = response.data.portfolios;
        var data = jsonData.map((item) => {
          const { create_at } = item;
          const date = new Date(create_at);
          const now = new Date();
          const diff = now.getTime() - date.getTime();
          const days = Math.floor(diff / (1000 * 60 * 60 * 24));

          return [
            item?.name,
            item?.contracts?.name,
            new Date(item?.create_at),
            days,
            item?.countClints ? item?.countClints : 0,
            item?.countClintsAmount ? item?.countClintsAmount.toFixed(2) : 0,
            item?.payments ? item?.payments.toFixed(2) : 0,
            item?.countClintsAmount && item?.payments
              ? (item?.countClintsAmount - item?.payments).toFixed(2)
              : 0,
          ];
        });

        // Add title row
        data.unshift(
          ["برنامج التحصيل الذكي |  تقرير المحافظ"],
          [
            "المحفظة ",
            " العقد",
            "تاريخ الاسناد ",
            "ايام الاسناد ",
            "عدد العملاء",
            "مبلغ المديونية",
            "المبلغ المحصل",
            "المتبقي",
          ]
        );
        var ws = wb.addWorksheet("Portfolios");
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
        saveAs(blob, "Portfolios.xlsx");

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
