import { useState } from "react";
import Table from "@/components/ui/Table";
import Badge from "@/components/ui/Badge";
import {
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
  createColumnHelper,
} from "@tanstack/react-table";
import { NumericFormat } from "react-number-format";
import {
  useAppSelector,
  OrderHistory,
  getLoginLogs,
  useAppDispatch,
} from "../store";
import dayjs from "dayjs";
import { useTranslation } from "react-i18next";

const { Tr, Th, Td, THead, TBody, Sorter } = Table;

const statusColor: Record<string, string> = {
  success: "bg-emerald-500",
  user_not_active: "bg-amber-400",
};

const LoginLogs = (id) => {
  const dispatch = useAppDispatch();
  const { t } = useTranslation();
  const columnHelper = createColumnHelper<OrderHistory>();

  const columns = [
    columnHelper.accessor("id", {
      header: t("loginLogs.id"),
      cell: (props) => {
        const row = props.row.original;
        return (
          <div>
            <span className="cursor-pointer">{row.id}</span>
          </div>
        );
      },
    }),
    columnHelper.accessor("username", {
      header: t("loginLogs.user"),
    }),
    columnHelper.accessor("outcome", {
      header: t("loginLogs.status"),
      cell: (props) => {
        const row = props.row.original;
        return (
          <div className="flex items-center">
            <Badge
              className={
                statusColor[row.outcome.toLowerCase().replace(/\s+/g, "_")]
              }
            />
            <span className="ml-2 rtl:mr-2 capitalize">
              {t("loginLogs." + row.outcome.toLowerCase().replace(/\s+/g, "_"))}
            </span>
          </div>
        );
      },
    }),
    columnHelper.accessor("timestamp", {
      header: t("loginLogs.date"),
      cell: (props) => {
        const row = props.row.original;
        return (
          <div className="flex items-center">
            <span>{dayjs(row.timestamp).format("DD/MM/YYYY HH:mm:ss ")}</span>
          </div>
        );
      },
    }),
    columnHelper.accessor("ip_address", {
      header: t("loginLogs.ip"),
    }),
  ];
  const data = useAppSelector(
    (state) => state.crmCustomerDetails.data.LoginLogsData
  );

  useState(() => {
    dispatch(getLoginLogs(id));
  });

  const [sorting, setSorting] = useState<
    {
      id: string;
      desc: boolean;
    }[]
  >([]);

  const table = useReactTable({
    data,
    columns,
    state: {
      sorting,
    },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });

  return (
    <div className="mb-8">
      <h6 className="mb-4 text-[15px] font-extrabold text-navy-600 dark:text-night-body">
        {t("loginLogs.title")}
      </h6>
      <Table>
        <THead>
          {table.getHeaderGroups().map((headerGroup) => (
            <Tr key={headerGroup.id}>
              {headerGroup.headers.map((header) => {
                return (
                  <Th key={header.id} colSpan={header.colSpan}>
                    {header.isPlaceholder ? null : (
                      <div
                        {...{
                          className: header.column.getCanSort()
                            ? "cursor-pointer select-none"
                            : "",
                          onClick: header.column.getToggleSortingHandler(),
                        }}
                      >
                        {flexRender(
                          header.column.columnDef.header,
                          header.getContext()
                        )}
                        {<Sorter sort={header.column.getIsSorted()} />}
                      </div>
                    )}
                  </Th>
                );
              })}
            </Tr>
          ))}
        </THead>
        <TBody>
          {table
            .getRowModel()
            .rows.slice(0, 10)
            .map((row) => {
              return (
                <Tr key={row.id}>
                  {row.getVisibleCells().map((cell) => {
                    return (
                      <Td key={cell.id}>
                        {flexRender(
                          cell.column.columnDef.cell,
                          cell.getContext()
                        )}
                      </Td>
                    );
                  })}
                </Tr>
              );
            })}
        </TBody>
      </Table>
    </div>
  );
};

export default LoginLogs;
