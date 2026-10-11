import { useContext } from "react";
import ToastNotification from "@/components/shared/myComponents/ToastNotification";
import { useState, useEffect } from "react";
import Button from "@/components/ui/Button";
import { useTranslation } from "react-i18next";
import { HiPlusCircle, HiDownload } from "react-icons/hi";
import { apiGetUsersExport } from "@/services/UserService";


function ExportByExcel({
  pramas,
  notificationFailedMessage,
  notificationTitle,
  notificationSuccessMessage,
}) {
  const { t } = useTranslation();
  const [isLoading, setIsLoading] = useState(false);

  const fetchData = async () => {
    await apiGetUsersExport({ filterData: pramas })
      .then(async (response) => {
        const ExcelJS = (await import("exceljs")).default;
        const { saveAs } = await import("file-saver");
        var wb = new ExcelJS.Workbook();
        var jsonData = response.data.users;
        var data = jsonData.map((item) => {
          return [
            item?.branch?.name,
            item?.count_clints ? item?.count_clints : 0,
            item?.total_amount ? item?.total_amount : 0,
            item?.email ? item?.email : "",
            item?.extension ? item?.extension : "",
            item?.name ? item?.name : "",
            item?.identityNumber ? item?.identityNumber : "",
            item?.is_active ? "نشط" : "غير نشط",
            item?.mobile ? item?.mobile : "",
            item?.nationality?.name ? item?.nationality?.name : "",
            item?.role?.name ? item?.role?.name : "",
            item?.user_name ? item?.user_name : "",
          ];
        });

        // Add title row
        data.unshift(
          ["برنامج التحصيل الذكي |  المستخدمين"],
          [
            "الفرع",
            "عدد العملاء",
            "اجمالي المديونية",
            "البريد الالكتروني",
            "التحويلة",
            "الاسم",
            "رقم الهوية",
            "الحالة",
            "الجوال",
            "الجنسية",
            "الصلاحية",
            "اسم المستخدم",
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
        saveAs(blob, "المستخدمين.xlsx");

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
