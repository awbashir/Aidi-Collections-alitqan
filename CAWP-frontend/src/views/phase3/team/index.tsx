/**
 * Team targets (`/team`, route key `team_any` = dashbord_admin | dashbord_user).
 *
 * GET /v2/targets/achievement?month=YYYY-MM[&contract_id=]: target vs actual per
 * collector for one Asia/Riyadh month. With `dashbord_admin` the API answers the
 * `all` view (every collector + a contracts table); otherwise the `own` view
 * (the caller's row only). Every figure is shown exactly as returned; the API
 * decides the target in force, the month lock, the rank and "unknown".
 * Sorting and the name search run on the returned rows (no paging); decimals
 * are compared as exact strings. Month, contract, search and sort live in the
 * URL hash.
 */
import classNames from "classnames";
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  HiOutlineChevronLeft,
  HiOutlineChevronRight,
  HiOutlineSortAscending,
  HiOutlineSortDescending,
} from "react-icons/hi";
import Button from "@/components/ui/Button";
import Input from "@/components/ui/Input";
import Table from "@/components/ui/Table";
import useHashState, { hashCodec } from "@/utils/hooks/useHashState";
import type { HashCodec, HashSchema } from "@/utils/hooks/useHashState";
import { adminApi, describeP3Error, lookups } from "@/services/v2";
import type {
  AchievementCollector,
  AchievementContract,
  AchievementTarget,
  Option,
} from "@/services/v2";
import { useV2Query } from "@/utils/v2/hooks";
import { formatCount, formatMoney, isNegativeMoney } from "@/utils/v2/format";
import { Ltr, MoneyText } from "@/components/v2/Display";
import { PlainBadge } from "@/components/v2/Badges";
import {
  EmptyState,
  ErrorState,
  Note,
  SkeletonRows,
} from "@/components/v2/States";
import { FilterSelect, PageHead, TableFrame } from "@/components/v2/ListParts";
import { MoneyKpi } from "@/views/phase1/money/listParts";
import { InForceText } from "@/views/phase3/targets/targetFormat";
import {
  MONTH_RE,
  compareDecimal,
  riyadhMonth,
  shiftMonth,
} from "@/views/phase3/targets/targetUtils";
import type { ReactNode } from "react";

const { THead, TBody, Tr, Th, Td } = Table;

const SORT_KEYS = [
  "rank",
  "name",
  "target",
  "actual",
  "recorded",
  "percent",
  "remaining",
] as const;
type SortKey = (typeof SORT_KEYS)[number];
const DIRS = ["asc", "desc"] as const;
type Dir = (typeof DIRS)[number];

const monthCodec: HashCodec<string | undefined> = {
  parse: (raw) => (MONTH_RE.test(raw) ? raw : undefined),
  serialize: (v) => (typeof v === "string" && MONTH_RE.test(v) ? v : ""),
};

type HashState = {
  month: string | undefined;
  contract: string | number | undefined;
  q: string | undefined;
  sort: SortKey;
  dir: Dir;
};

const schema: HashSchema<HashState> = {
  month: { key: "month", codec: monthCodec, default: undefined },
  contract: { key: "contract", codec: hashCodec.id(), default: undefined },
  q: {
    key: "q",
    codec: hashCodec.text({ maxLength: 80 }),
    default: undefined,
    history: "replace",
  },
  sort: { key: "sort", codec: hashCodec.oneOf(SORT_KEYS), default: "rank" },
  dir: { key: "dir", codec: hashCodec.oneOf(DIRS), default: "asc" },
};

/* ------------------------------------------------------------------ sorting */

/** The comparable value of a row for a sort key; `null` = unknown (always last). */
const sortValue = (
  c: AchievementCollector,
  key: SortKey
): string | number | null => {
  switch (key) {
    case "rank":
      return c.rank;
    case "name":
      return c.name || c.user_name || "";
    case "target":
      return c.target.known ? c.target.amount : null;
    case "actual":
      return c.actual;
    case "recorded":
      return c.actual_recorded;
    case "percent":
      return c.achievement_percent;
    case "remaining":
      return c.remaining;
    default:
      return null;
  }
};

function sortCollectors(
  rows: AchievementCollector[],
  key: SortKey,
  dir: Dir
): AchievementCollector[] {
  const sign = dir === "asc" ? 1 : -1;
  return rows
    .map((row, i) => ({ row, i, v: sortValue(row, key) }))
    .sort((a, b) => {
      // unknown / null last in both directions
      if (a.v === null || a.v === undefined)
        return b.v === null || b.v === undefined ? a.i - b.i : 1;
      if (b.v === null || b.v === undefined) return -1;
      let c = 0;
      if (typeof a.v === "number" && typeof b.v === "number") c = a.v - b.v;
      else if (key === "name") c = String(a.v).localeCompare(String(b.v), "ar");
      else c = compareDecimal(String(a.v), String(b.v));
      return c !== 0 ? c * sign : a.i - b.i;
    })
    .map((x) => x.row);
}

/* -------------------------------------------------------------------- cells */

const Percent = ({ value }: { value: string | null }) =>
  value === null ? (
    <span className="text-gray-500">—</span>
  ) : (
    <span
      dir="ltr"
      className="tabular-nums font-extrabold text-navy-600 dark:text-gray-100"
    >
      {value}%
    </span>
  );

/** Remaining as returned; negative means the target was exceeded. */
const Remaining = ({ value }: { value: string | null }) => {
  const { t } = useTranslation();
  if (value === null) return <span className="text-gray-500">—</span>;
  if (isNegativeMoney(value))
    return (
      <span
        className="inline-flex flex-col items-end gap-0.5"
        data-exceeded="true"
      >
        <span
          dir="ltr"
          className="tabular-nums whitespace-nowrap font-extrabold text-success"
        >
          {formatMoney(value)}
        </span>
        <span className="text-[11.5px] font-extrabold text-success">
          {t("p3.team.exceeded")}
        </span>
      </span>
    );
  return <MoneyText value={value} />;
};

/** The target cell: exact amount, then where it comes from. Unknown never shows 0. */
const TargetCell = ({
  target,
  subject,
}: {
  target: AchievementTarget;
  subject: "user" | "contract";
}) => {
  const { t } = useTranslation();
  if (!target.known)
    return (
      <span
        className="font-extrabold text-warning"
        data-testid="p3-no-target-history"
      >
        {t("p3.targets.noHistory")}
      </span>
    );
  const inForce =
    subject === "contract"
      ? target.contract_target
      : target.source === "user"
      ? target.user_target
      : target.source === "contract"
      ? target.contract_target
      : undefined;
  let origin: ReactNode;
  if (subject === "contract") origin = null;
  else if (target.source === "user") origin = t("p3.team.fromUser");
  else if (target.source === "contract") origin = t("p3.team.fromContract");
  else origin = t("p3.team.fromNone");
  return (
    <div className="flex flex-col items-end gap-0.5 text-end">
      <MoneyText value={target.amount} strong />
      <span className="max-w-[280px] text-[11.5px] leading-snug text-gray-600">
        {origin && <span className="font-extrabold">{origin}</span>}
        {inForce && inForce.known && (
          <>
            {origin ? " · " : ""}
            <InForceText
              target={inForce}
              subject={subject}
              percentBase={target.percent_base}
              showKind={false}
            />
          </>
        )}
      </span>
    </div>
  );
};

const SortTh = ({
  k,
  sort,
  dir,
  onSort,
  className,
  children,
}: {
  k: SortKey;
  sort: SortKey;
  dir: Dir;
  onSort: (k: SortKey) => void;
  className?: string;
  children: ReactNode;
}) => (
  <Th
    className={className}
    aria-sort={
      sort === k ? (dir === "asc" ? "ascending" : "descending") : undefined
    }
  >
    <button
      type="button"
      onClick={() => onSort(k)}
      className={classNames(
        "inline-flex items-center gap-1 uppercase",
        sort === k && "text-azure-600"
      )}
      data-sort-key={k}
    >
      {children}
      {sort === k &&
        (dir === "asc" ? (
          <HiOutlineSortAscending aria-hidden />
        ) : (
          <HiOutlineSortDescending aria-hidden />
        ))}
    </button>
  </Th>
);

/* ------------------------------------------------------------------- screen */

const Team = () => {
  const { t } = useTranslation();
  const { state, setState } = useHashState(schema);
  const thisMonth = riyadhMonth();
  const month = state.month || thisMonth;
  const contract =
    state.contract === undefined ? undefined : String(state.contract);

  const [text, setText] = useState(state.q || "");
  useEffect(() => {
    setText(state.q || "");
  }, [state.q]);

  const [contracts, setContracts] = useState<Option[]>([]);
  const [contractsLoading, setContractsLoading] = useState(true);
  useEffect(() => {
    let alive = true;
    lookups
      .contracts()
      .then((c) => alive && setContracts(c))
      .catch(() => undefined)
      .finally(() => alive && setContractsLoading(false));
    return () => {
      alive = false;
    };
  }, []);

  const query = useV2Query(
    (signal) => adminApi.achievement({ month, contract_id: contract }, signal),
    [month, contract]
  );
  const data = query.data;
  const isAll = data?.view === "all";
  const isOwn = data?.view === "own";

  const rows = useMemo(() => {
    if (!data) return [];
    const needle = text.trim().toLowerCase();
    const filtered = needle
      ? data.collectors.filter(
          (c) =>
            (c.name || "").toLowerCase().includes(needle) ||
            (c.user_name || "").toLowerCase().includes(needle)
        )
      : data.collectors;
    return sortCollectors(filtered, state.sort, state.dir);
  }, [data, text, state.sort, state.dir]);

  const onSort = (k: SortKey) =>
    setState(
      k === state.sort
        ? { dir: state.dir === "asc" ? "desc" : "asc" }
        : { sort: k, dir: k === "name" || k === "rank" ? "asc" : "desc" }
    );

  const totals = data?.totals;
  const period = data?.period;
  const cols = 8;
  const sortOptions = SORT_KEYS.map((k) => ({
    value: k,
    label: t(`p3.team.sort.${k}`),
  }));

  return (
    <div
      data-testid="p3-team"
      data-view={data?.view ?? ""}
      data-month={data?.month ?? ""}
    >
      <PageHead
        title={t("p3.team.title")}
        subtitle={
          period ? (
            <span>
              {t("p3.team.subtitle", { tz: period.timezone })}{" "}
              <Ltr>
                {period.first_day} – {period.last_day}
              </Ltr>
            </span>
          ) : (
            t("p3.team.subtitleLoading")
          )
        }
        onRefresh={query.reload}
        refreshing={query.loading && !!data}
      />

      {/* filters */}
      <div
        className="mb-4 flex flex-wrap items-end gap-3"
        data-testid="p3-team-filters"
      >
        <div>
          <div className="text-eyebrow mb-1">{t("p3.fields.month")}</div>
          <div className="flex items-center gap-1">
            <Button
              size="sm"
              type="button"
              icon={<HiOutlineChevronLeft className="rtl:rotate-180" />}
              aria-label={String(t("p3.team.prevMonth"))}
              title={String(t("p3.team.prevMonth"))}
              onClick={() => {
                const prev = shiftMonth(month, -1);
                setState({ month: prev === thisMonth ? undefined : prev });
              }}
              data-testid="p3-team-month-prev"
            />
            <Input
              size="sm"
              type="month"
              dir="ltr"
              className="w-[160px]"
              value={month}
              onChange={(e) => {
                const v = e.target.value;
                if (MONTH_RE.test(v))
                  setState({ month: v === thisMonth ? undefined : v });
              }}
              data-testid="p3-team-month"
            />
            <Button
              size="sm"
              type="button"
              icon={<HiOutlineChevronRight className="rtl:rotate-180" />}
              aria-label={String(t("p3.team.nextMonth"))}
              title={String(t("p3.team.nextMonth"))}
              onClick={() => {
                const next = shiftMonth(month, 1);
                setState({ month: next === thisMonth ? undefined : next });
              }}
              data-testid="p3-team-month-next"
            />
            {month !== thisMonth && (
              <Button
                size="sm"
                variant="plain"
                type="button"
                onClick={() => setState({ month: undefined })}
                data-testid="p3-team-month-current"
              >
                {t("p3.team.thisMonth")}
              </Button>
            )}
          </div>
        </div>
        {period?.locked && (
          <div className="pb-1.5" data-testid="p3-team-month-locked">
            <PlainBadge tone="neutral">{t("p3.team.closedMonth")}</PlainBadge>
          </div>
        )}
        {isAll && (
          <FilterSelect
            label={t("p1.filter.contract")}
            options={contracts}
            loading={contractsLoading}
            value={contract}
            testId="p3-team-contract"
            onChange={(v) => setState({ contract: v })}
          />
        )}
        {!isOwn && (
          <div className="w-[220px]">
            <div className="text-eyebrow mb-1">{t("p3.team.search")}</div>
            <Input
              size="sm"
              value={text}
              placeholder={String(t("p3.team.searchPlaceholder"))}
              onChange={(e) => {
                setText(e.target.value);
                setState({
                  q: e.target.value.trim() ? e.target.value : undefined,
                });
              }}
              data-testid="p3-team-search"
            />
          </div>
        )}
        <div>
          <div className="text-eyebrow mb-1">{t("p3.fields.sort")}</div>
          <div className="flex items-center gap-1">
            <select
              className="h-9 rounded-md border border-gray-400 bg-white px-3 text-[13.5px] text-gray-700 focus:border-azure-600 focus:outline-none dark:border-night-border dark:bg-night-card dark:text-night-body"
              value={state.sort}
              onChange={(e) => onSort(e.target.value as SortKey)}
              data-testid="p3-team-sort"
            >
              {sortOptions.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
            <Button
              size="sm"
              type="button"
              icon={
                state.dir === "asc" ? (
                  <HiOutlineSortAscending />
                ) : (
                  <HiOutlineSortDescending />
                )
              }
              onClick={() =>
                setState({ dir: state.dir === "asc" ? "desc" : "asc" })
              }
              data-testid="p3-team-sort-dir"
              data-dir={state.dir}
            >
              {state.dir === "asc" ? t("p3.team.asc") : t("p3.team.desc")}
            </Button>
          </div>
        </div>
        {(contract || text) && (
          <Button
            size="sm"
            variant="plain"
            type="button"
            onClick={() => {
              setText("");
              setState({ contract: undefined, q: undefined });
            }}
          >
            {t("p1.filter.clear")}
          </Button>
        )}
      </div>

      {query.error && !data ? (
        <div className="rounded-lg border border-gray-300 bg-white dark:border-night-border dark:bg-night-card">
          <ErrorState
            error={query.error}
            onRetry={query.reload}
            describe={describeP3Error}
          />
        </div>
      ) : (
        <>
          {isOwn && (
            <Note className="mb-3">
              <span data-testid="p3-team-own-note">{t("p3.team.ownNote")}</span>
            </Note>
          )}

          {/* KPIs */}
          <div
            className="mb-4 grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-5"
            data-testid="p3-team-kpis"
          >
            <MoneyKpi
              label={t("p3.team.kpiTarget")}
              // no row, or every target unknown: the sum of known targets (0.00) is not a target, show "—"
              value={
                totals &&
                (totals.collectors === 0 ||
                  totals.targets_unknown === totals.collectors)
                  ? undefined
                  : totals?.target
              }
              testId="p3-team-kpi-target"
              sub={
                totals ? (
                  <span className="flex flex-col gap-0.5">
                    <span>
                      {t("p3.team.kpiCollectors", {
                        n: formatCount(totals.collectors),
                      })}
                    </span>
                    {totals.targets_unknown > 0 && (
                      <span
                        className="font-extrabold text-warning"
                        data-testid="p3-team-targets-unknown"
                        data-count={totals.targets_unknown}
                      >
                        {t("p3.team.kpiUnknown", {
                          n: formatCount(totals.targets_unknown),
                        })}
                      </span>
                    )}
                    <span>{t("p3.team.kpiTargetKnownOnly")}</span>
                  </span>
                ) : undefined
              }
            />
            <MoneyKpi
              label={t("p3.team.kpiActual")}
              value={totals?.actual}
              lead
              testId="p3-team-kpi-actual"
              sub={t("p3.team.kpiActualSub")}
            />
            <MoneyKpi
              label={t("p3.team.kpiRecorded")}
              value={totals?.actual_recorded}
              testId="p3-team-kpi-recorded"
              sub={t("p3.team.kpiRecordedSub")}
            />
            {isAll &&
              totals?.all_approved_recovery !== null &&
              totals?.all_approved_recovery !== undefined && (
                <MoneyKpi
                  label={t("p3.team.kpiAllApproved")}
                  value={totals.all_approved_recovery}
                  testId="p3-team-kpi-all-approved"
                  sub={t("p3.team.kpiAllApprovedSub")}
                />
              )}
            {isAll &&
              totals?.unassigned_cases_recovery !== null &&
              totals?.unassigned_cases_recovery !== undefined && (
                <MoneyKpi
                  label={t("p3.team.kpiUnassigned")}
                  value={totals.unassigned_cases_recovery}
                  testId="p3-team-kpi-unassigned"
                  sub={t("p3.team.kpiUnassignedSub")}
                />
              )}
          </div>

          <div
            className="mb-4 rounded-lg border border-gray-300 bg-white px-5 py-4 dark:border-night-border dark:bg-night-card"
            data-testid="p3-team-actual-note"
          >
            <div className="text-eyebrow mb-2">{t("p3.team.howTitle")}</div>
            <ul className="mb-0 list-disc space-y-1 ps-5 text-[12.5px] leading-relaxed text-gray-700 dark:text-night-body">
              <li>{t("p3.team.howActual")}</li>
              <li>{t("p3.team.howRecorded")}</li>
              <li>{t("p3.team.howDiffer")}</li>
              <li>{t("p3.team.howTarget")}</li>
              <li>{t("p3.team.howPercent")}</li>
              <li>{t("p3.team.howUnknown")}</li>
              <li>{t("p3.team.howMoney")}</li>
            </ul>
          </div>

          {/* collectors */}
          <TableFrame
            testId="p3-team-collectors"
            toolbar={
              <>
                <div className="text-[15px] font-extrabold text-navy-600 dark:text-gray-100">
                  {t("p3.team.collectorsTitle")}
                </div>
                {data && (
                  <span
                    className="text-[12.5px] text-gray-600 tabular-nums"
                    data-testid="p3-team-count"
                  >
                    {rows.length === data.collectors.length
                      ? t("p3.team.rowsCount", { n: formatCount(rows.length) })
                      : t("p3.team.rowsFiltered", {
                          n: formatCount(rows.length),
                          total: formatCount(data.collectors.length),
                        })}
                  </span>
                )}
              </>
            }
          >
            <Table>
              <THead>
                <Tr>
                  <SortTh
                    k="rank"
                    sort={state.sort}
                    dir={state.dir}
                    onSort={onSort}
                  >
                    {t("p3.team.col.rank")}
                  </SortTh>
                  <SortTh
                    k="name"
                    sort={state.sort}
                    dir={state.dir}
                    onSort={onSort}
                  >
                    {t("p3.team.col.collector")}
                  </SortTh>
                  <Th>{t("p3.team.col.contract")}</Th>
                  <SortTh
                    k="target"
                    sort={state.sort}
                    dir={state.dir}
                    onSort={onSort}
                    className="text-end"
                  >
                    {t("p3.team.col.target")}
                  </SortTh>
                  <SortTh
                    k="actual"
                    sort={state.sort}
                    dir={state.dir}
                    onSort={onSort}
                    className="text-end"
                  >
                    {t("p3.team.col.actual")}
                  </SortTh>
                  <SortTh
                    k="recorded"
                    sort={state.sort}
                    dir={state.dir}
                    onSort={onSort}
                    className="text-end"
                  >
                    {t("p3.team.col.recorded")}
                  </SortTh>
                  <SortTh
                    k="percent"
                    sort={state.sort}
                    dir={state.dir}
                    onSort={onSort}
                    className="text-end"
                  >
                    {t("p3.team.col.percent")}
                  </SortTh>
                  <SortTh
                    k="remaining"
                    sort={state.sort}
                    dir={state.dir}
                    onSort={onSort}
                    className="text-end"
                  >
                    {t("p3.team.col.remaining")}
                  </SortTh>
                </Tr>
              </THead>
              <TBody>
                {query.loading && !data ? (
                  <SkeletonRows cols={cols} />
                ) : rows.length === 0 ? (
                  <tr>
                    <td colSpan={cols}>
                      {isOwn ? (
                        <EmptyState
                          title={t("p3.team.ownEmptyTitle")}
                          hint={t("p3.team.ownEmptyHint")}
                        />
                      ) : data && data.collectors.length > 0 ? (
                        <EmptyState
                          title={t("p3.team.noMatchTitle")}
                          hint={t("p3.team.noMatchHint")}
                        />
                      ) : (
                        <EmptyState
                          title={t("p3.team.emptyTitle")}
                          hint={t("p3.team.emptyHint")}
                        />
                      )}
                    </td>
                  </tr>
                ) : (
                  rows.map((c) => (
                    <Tr
                      key={c.user_id}
                      data-testid="p3-team-collector-row"
                      data-user={c.user_id}
                      data-known={String(c.target.known)}
                      data-rank={c.rank}
                    >
                      <Td>
                        <Ltr className="font-extrabold text-navy-600 dark:text-gray-100">
                          {c.rank}
                        </Ltr>
                      </Td>
                      <Td>
                        <div className="font-extrabold text-navy-600 dark:text-gray-100">
                          {c.name || "—"}
                        </div>
                        <Ltr className="text-[12px] text-gray-600">
                          {c.user_name}
                        </Ltr>
                      </Td>
                      <Td>
                        <span dir="auto">{c.contract_name || "—"}</span>
                      </Td>
                      <Td className="text-end">
                        <TargetCell target={c.target} subject="user" />
                      </Td>
                      <Td className="text-end">
                        <MoneyText value={c.actual} strong />
                        <div className="text-[11.5px] text-gray-600 tabular-nums">
                          {t("p3.team.payments", {
                            n: formatCount(c.payments_count),
                          })}
                        </div>
                      </Td>
                      <Td className="text-end">
                        <MoneyText value={c.actual_recorded} />
                        <div className="text-[11.5px] text-gray-600 tabular-nums">
                          {t("p3.team.payments", {
                            n: formatCount(c.recorded_count),
                          })}
                        </div>
                      </Td>
                      <Td className="text-end">
                        <Percent
                          value={c.target.known ? c.achievement_percent : null}
                        />
                      </Td>
                      <Td className="text-end">
                        <Remaining
                          value={c.target.known ? c.remaining : null}
                        />
                      </Td>
                    </Tr>
                  ))
                )}
              </TBody>
            </Table>
          </TableFrame>
          {query.error && data ? (
            <ErrorState
              error={query.error}
              onRetry={query.reload}
              describe={describeP3Error}
            />
          ) : null}

          {/* contracts (all view only) */}
          {isAll && data && <ContractsTable rows={data.contracts} />}
        </>
      )}
    </div>
  );
};

const ContractsTable = ({ rows }: { rows: AchievementContract[] }) => {
  const { t } = useTranslation();
  const cols = 6;
  return (
    <div className="mt-8">
      <TableFrame
        testId="p3-team-contracts"
        toolbar={
          <>
            <div className="text-[15px] font-extrabold text-navy-600 dark:text-gray-100">
              {t("p3.team.contractsTitle")}
            </div>
            <span className="text-[12.5px] text-gray-600 tabular-nums">
              {t("p3.team.rowsCount", { n: formatCount(rows.length) })}
            </span>
          </>
        }
      >
        <Table>
          <THead>
            <Tr>
              <Th>{t("p3.team.col.contract")}</Th>
              <Th className="text-end">{t("p3.team.col.target")}</Th>
              <Th
                className="text-end"
                title={String(t("p3.team.collectorsSumHint"))}
              >
                {t("p3.team.col.collectorsSum")}
              </Th>
              <Th className="text-end">{t("p3.team.col.actual")}</Th>
              <Th className="text-end">{t("p3.team.col.percent")}</Th>
              <Th className="text-end">{t("p3.team.col.remaining")}</Th>
            </Tr>
          </THead>
          <TBody>
            {rows.length === 0 ? (
              <tr>
                <td colSpan={cols}>
                  <EmptyState title={t("p3.team.contractsEmpty")} />
                </td>
              </tr>
            ) : (
              rows.map((c) => (
                <Tr
                  key={c.contract_id}
                  data-testid="p3-team-contract-row"
                  data-contract={c.contract_id}
                  data-known={String(c.target.known)}
                >
                  <Td>
                    <span className="flex flex-wrap items-center gap-2">
                      <span
                        dir="auto"
                        className="font-extrabold text-navy-600 dark:text-gray-100"
                      >
                        {c.name}
                      </span>
                      {!c.is_active && (
                        <PlainBadge tone="neutral">
                          {t("p3.team.inactive")}
                        </PlainBadge>
                      )}
                    </span>
                  </Td>
                  <Td className="text-end">
                    <TargetCell target={c.target} subject="contract" />
                  </Td>
                  <Td className="text-end">
                    <MoneyText value={c.collectors_target_sum} />
                  </Td>
                  <Td className="text-end">
                    <MoneyText value={c.actual} strong />
                    <div className="text-[11.5px] text-gray-600 tabular-nums">
                      {t("p3.team.payments", {
                        n: formatCount(c.payments_count),
                      })}
                    </div>
                  </Td>
                  <Td className="text-end">
                    <Percent
                      value={c.target.known ? c.achievement_percent : null}
                    />
                  </Td>
                  <Td className="text-end">
                    <Remaining value={c.target.known ? c.remaining : null} />
                  </Td>
                </Tr>
              ))
            )}
          </TBody>
        </Table>
      </TableFrame>
      <Note className="mt-3">
        <span data-testid="p3-team-collectors-sum-note">
          {t("p3.team.collectorsSumNote")}
        </span>
      </Note>
    </div>
  );
};

export default Team;
