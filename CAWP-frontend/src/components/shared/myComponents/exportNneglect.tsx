import { useContext } from "react";
import ToastNotification from "@/components/shared/myComponents/ToastNotification";
import { useState, useEffect } from "react";
import Button from "@/components/ui/Button";
import { useTranslation } from "react-i18next";
import { HiPlusCircle, HiDownload } from "react-icons/hi";
import { apiGetClientsNneglectExport } from "@/services/ReportsService";


function ExportByExcel({
  pramas,
  notificationFailedMessage,
  notificationTitle,
  notificationSuccessMessage,
}) {
  const { t } = useTranslation();
  const [isLoading, setIsLoading] = useState(false);

  const fetchData = async () => {
    await apiGetClientsNneglectExport({ filterData: pramas })
      .then(async (response) => {
        const ExcelJS = (await import("exceljs")).default;
        const { saveAs } = await import("file-saver");
        var wb = new ExcelJS.Workbook();
        var jsonData = response.data.clints;
        var data = jsonData.map((item) => {
          return [
            item?.portfolios?.name,
            item?.accountNumber,
            item?.name,
            item?.identityNumber,

            item?.contract.name,
            item?.comment_status?.name,
            item?.comment?.comment ? item?.comment?.comment : "",
            item?.amount,

            item?.user?.name,
            item?.comment?.create_at ? new Date(item?.comment?.create_at) : "",
            // acount days
            item?.comment?.create_at
              ? Math.floor(
                  (new Date().getTime() -
                    new Date(item?.comment?.create_at).getTime()) /
                    (1000 * 60 * 60 * 24)
                )
              : 0,
          ];
        });

        // Add title row
        data.unshift(
          ["فنيه الاتقان لتحصيل الديون |  العملاء الغير نشطين"],
          [
            "المحفظة",
            "رقم الحساب",
            "اسم العميل",
            "رقم الهوية",
            "اسم العقد",
            "حالة العميل",
            "نص المتابعة",
            "مبلغ المديونية",

            "المستخدم",
            "تاريخ اخر متابعة",
            "أخر متابعة منذ",
          ]
        );
        var ws = wb.addWorksheet("تقرير الاهمال");
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
        saveAs(blob, "تقرير الاهمال.xlsx");

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
