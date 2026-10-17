import { useContext } from "react";
import ToastNotification from "@/components/shared/myComponents/ToastNotification";
import { useState, useEffect } from "react";
import Button from "@/components/ui/Button";
import { useTranslation } from "react-i18next";
import { HiPlusCircle, HiDownload } from "react-icons/hi";
import { apiGetClientsArchiveExport } from "@/services/ArchiveService";


function ExportByExcel({
  pramas,
  filterData,
  notificationFailedMessage,
  notificationTitle,
  notificationSuccessMessage,
}) {
  const { t } = useTranslation();
  const [isLoading, setIsLoading] = useState(false);

  const fetchData = async () => {
    setIsLoading(true);
    await apiGetClientsArchiveExport({ filterData: pramas })
      .then(async (response) => {
        const ExcelJS = (await import("exceljs")).default;
        const { saveAs } = await import("file-saver");
        var wb = new ExcelJS.Workbook();
        var jsonData = response.data.clients;
        var data = jsonData.map((item) => {
          return [
            item?.portfolios?.name,
            item?.contract.name,
            item?.clintArchive?.user?.name,
            item?.clintArchive?.archive_reason?.name,
            item?.clintArchive?.create_at
              ? new Date(item?.clintArchive?.create_at)
              : "",
            item?.user?.name,
            item?.comment_status?.name,
            item?.comment_sub_status?.name || "",
            item?.accountNumber,
            item?.name,
            item?.identityNumber,

            item?.amount,
            item?.id,
          ];
        });

        // Add title row
        data.unshift(
          ["برنامج التحصيل الذكي |  العملاء الأرشف"],
          [
            "المحفظة",
            "اسم العقد",
            "قام بالنقل",
            "سبب النقل",
            "تاريخ النقل",
            "المستخدم",
            "حالة العميل",
            "الحالة الفرعية",
            "رقم الحساب",
            "اسم العميل",
            "رقم الهوية",
            "مبلغ المديونية",
            "المعرف",
          ]
        );
        var ws = wb.addWorksheet("عملاء الأرشيف");
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

        const now = new Date();
        const year = now.getFullYear();
        const month = String(now.getMonth() + 1).padStart(2, "0"); // Months are zero-based
        const day = String(now.getDate()).padStart(2, "0");
        const formattedDate = `${year}${month}${day}`;

        const fileName = `Archive_Clients_${formattedDate}.xlsx`;
        saveAs(blob, fileName);

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
