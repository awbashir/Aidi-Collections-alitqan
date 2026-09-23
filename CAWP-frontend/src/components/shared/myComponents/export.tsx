import { useContext } from "react";
import ToastNotification from "@/components/shared/myComponents/ToastNotification";

import { useState, useEffect } from "react";

import { apiGetClientsArchiveExport } from "@/services/ArchiveService";


import Button from "@/components/ui/Button";
import { useTranslation } from "react-i18next";
import { HiPlusCircle, HiDownload } from "react-icons/hi";

function ExportExcel({
  pramas,
  notificationFailedMessage,
  notificationTitle,
  notificationSuccessMessage,
}) {
  const { t } = useTranslation();

  const [isLoading, setIsLoading] = useState(false);

  const fetchData = async () => {
    setIsLoading(true);
    await apiGetClientsArchiveExport(pramas)
      .then(async (response) => {
        const ExcelJS = (await import("exceljs")).default;
        const { saveAs } = await import("file-saver");
        var wb = new ExcelJS.Workbook();
        var jsonData = response.data.clients;
        var data = jsonData.map((item) => {
          var days = new Date(item?.specifyUser?.create_at);
          var days2 = new Date();
          var timeDiff = Math.abs(days2.getTime() - days.getTime());
          var diffDays = Math.ceil(timeDiff / (1000 * 3600 * 24));

          return {
            المحفظة: item.portfolios.name,
            العقد: item.contract.name,
            المستخدم: item.user?.name,
            "ايام الاسناد": diffDays ? diffDays : 0,
            الحالة: item.comment_status?.name,
            "رقم الحساب": item.accountNumber,
            "مبلغ المديونية": item.amount,
            "إسم العميل": item.name,
            "رقم هوية العميل": item.identityNumber,
            "رقم الخدمة": item.attribute?.MSISDN,
            "حالة الخط": item.attribute?.MNP,
            "تاريخ الاسناد": item.attribute.BATCH_DATE
              ? new Date(item.attribute?.BATCH_DATE)
              : "",
            "جوال العميل": item.attribute?.mobile_1,
            "رقم التواصل": item?.comment?.contactNumber,
          };
        });

        var headers = [
          "المحفظة",
          "العقد",
          "المستخدم",
          "ايام الاسناد",
          "الحالة",
          "رقم الحساب",
          "مبلغ المديونية",
          "إسم العميل",
          "رقم هوية العميل",
          "رقم الخدمة",
          "حالة الخط",
          "تاريخ الاسناد",
          "جوال العميل",
          "رقم التواصل",
        ];
        var ws = wb.addWorksheet("clints");
        ws.views = [{ rightToLeft: true }];
        ws.addRow(headers);
        data.forEach((item: any) => {
          ws.addRow(headers.map((key) => item[key]));
        });

        const buffer = await wb.xlsx.writeBuffer();
        const blob = new Blob([buffer], {
          type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        });
        saveAs(blob, "clints.xlsx");
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

export default ExportExcel;
