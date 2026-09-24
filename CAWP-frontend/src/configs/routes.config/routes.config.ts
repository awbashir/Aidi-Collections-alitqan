import { lazy } from "react";
import authRoute from "./authRoute";
import type { Routes } from "@/@types/routes";
import { APP_PREFIX_PATH } from "@/constants/route.constant";

export const publicRoutes: Routes = [...authRoute];

export const protectedRoutes = [
  {
    key: "dashbord",
    path: "/home",
    component: lazy(() => import("@/views/dashboard2")),
    authority: [],
  },
  {
    key: "users",
    path: "/users",
    component: lazy(() => import("@/views/users")),
    authority: [],
  },
  {
    key: "users_details",
    path: "/users/details",
    component: lazy(() => import("@/views/users/detail/CustomerDetail")),
    authority: [],
    meta: {
      header: "users.details",
      headerContainer: true,
    },
  },
  {
    key: "users_edit",
    path: "/users/edit",
    component: lazy(() => import("@/views/users/edit/UserEdit")),
    authority: [],
    meta: {
      header: "users.edit",
      headerContainer: true,
    },
  },
  {
    key: "users_add",
    path: "/users/new",
    component: lazy(() => import("@/views/users/new/UserNew")),
    authority: [],
    meta: {
      header: "users.addNew",
      headerContainer: true,
    },
  },

  {
    key: "contracts",
    path: "/contracts",
    component: lazy(() => import("@/views/contracts")),
    authority: [],

    meta: {
      header: "contracts.title",
      headerContainer: true,
    },
  },

  {
    key: "contracts_add",
    path: "/contracts/new",
    component: lazy(() => import("@/views/contracts/new/UserNew")),
    authority: [],
    meta: {
      header: "contracts.new",
      headerContainer: true,
    },
  },
  {
    key: "contracts_edit",
    path: "/contracts/edit",
    component: lazy(() => import("@/views/contracts/edit/ContractEdit")),
    authority: [],
    meta: {
      header: "contracts.edit",
      headerContainer: true,
    },
  },
  {
    key: "portfolios",
    path: "/portfolios",
    component: lazy(() => import("@/views/portfolios")),
    authority: [],

    meta: {
      header: "portfolios.title",
      headerContainer: true,
    },
  },

  {
    key: "portfolios_edit",
    path: "/portfolios/edit",
    component: lazy(() => import("@/views/portfolios/edit/PortfolioEdit")),
    authority: [],
    meta: {
      header: "portfolios.edit",
      headerContainer: true,
    },
  },

  {
    key: "clints",
    path: "/clients",
    component: lazy(() => import("@/views/clients")),
    authority: [],
    meta: {
      header: "clients.title",
      headerContainer: true,
    },
  },

  {
    key: "clints_details",
    path: "/clients/details/:tab",
    component: lazy(() => import("@/views/clients/detail/ClientDetail")),
    authority: [],
    meta: {
      header: "clients.details",
      headerContainer: true,
    },
  },

  {
    key: "clints_archive",
    path: "/archive",
    component: lazy(() => import("@/views/archive")),
    authority: [],
    meta: {
      header: "archive.title",
      headerContainer: true,
    },
  },

   {
    key: "client_query",
    path: "/client_query",
    component: lazy(() => import("@/views/clients2")),
    authority: [],
    meta: {
      header: "clients_query.title",
      headerContainer: true,
    },
  },

  {
    key: "branches",
    path: "/branch",
    component: lazy(() => import("@/views/branches")),
    authority: [],
    meta: {
      header: "branches.title",
      headerContainer: true,
    },
  },

  {
    key: "branches_add",
    path: "/branch/add",
    component: lazy(() => import("@/views/branches/add/UserNew")),
    authority: [],
    meta: {
      header: "branches.add",
      headerContainer: true,
    },
  },

  {
    key: "branches_edit",
    path: "/branch/edit",
    component: lazy(() => import("@/views/branches/edit/PortfolioEdit")),
    authority: [],
    meta: {
      header: "branches.edit",
      headerContainer: true,
    },
  },

  {
    key: "supervisors",
    path: "/supervisor",
    component: lazy(() => import("@/views/supervisors")),
    authority: [],
    meta: {
      header: "supervisors.title",
      headerContainer: true,
    },
  },

  {
    key: "supervisors_add",
    path: "/supervisor/add",
    component: lazy(() => import("@/views/supervisors/add/UserNew")),
    authority: [],
    meta: {
      header: "supervisors.add",
      headerContainer: true,
    },
  },

  {
    key: "supervisors_edit",
    path: "/supervisor/edit",
    component: lazy(() => import("@/views/supervisors/edit/supervisorEdit")),
    authority: [],
  },

  {
    key: "accounts_payments",
    path: "/payment",
    component: lazy(() => import("@/views/accounts")),
    authority: [],
    meta: {
      header: "payments.title",
      headerContainer: true,
    },
  },

  {
    key: "collections_users",
    path: "/user_collection",
    component: lazy(() => import("@/views/collections/users")),
    authority: [],
    meta: {
      header: "collections.title",
      headerContainer: true,
    },
  },

  {
    key: "collections_currentUser",
    path: "/current_user_collection",
    component: lazy(() => import("@/views/collections/currentUser")),
    authority: [],
    meta: {
      header: "collections.myCollections",
      headerContainer: true,
    },
  },
  {
    key: "report_users_comments",
    path: "/report/users/comments",
    component: lazy(() => import("@/views/reports/users/comments")),
    authority: [],
    meta: {
      header: "report.userReports.comments",
      headerContainer: true,
    },
  },

  {
    key: "report_users_paymentDates",
    path: "/report/users/paymentDates",
    component: lazy(() => import("@/views/reports/users/paymentDates")),
    authority: [],
    meta: {
      header: "report.userReports.paymentDates",
      headerContainer: true,
    },
  },

  {
    key: "report_clintes_active",
    path: "/report/clients/active",
    component: lazy(() => import("@/views/reports/clients/active")),
    authority: [],
    meta: {
      header: "report.clientReports.activeClients",
      headerContainer: true,
    },
  },

  {
    key: "report_clintes_inactive",
    path: "/report/clients/inactive",
    component: lazy(() => import("@/views/reports/clients/inactive")),
    authority: [],
    meta: {
      header: "report.clientReports.inactiveClients",
      headerContainer: true,
    },
  },

  {
    key: "report_portfolios",
    path: "/report/portfolios/portfolios",
    component: lazy(() => import("@/views/reports/portfolios")),
    authority: [],
    meta: {
      header: "report.portfolios.title",
      headerContainer: true,
    },
  },

  {
    key: "report_quality_neglect",
    path: "/report/quality/neglect",
    component: lazy(() => import("@/views/reports/quality/neglect")),
    authority: [],
    meta: {
      header: "report.qualityReports.neglectReport",
      headerContainer: true,
    },
  },
  {
    key: "report_collectors_currentUser_paymentDates",
    path: "/report/collectors/paymentDates",
    component: lazy(() => import("@/views/reports/collectors/paymentDates")),
    authority: [],
    meta: {
      header: "report.collectorReports.paymentDates",
      headerContainer: true,
    },
  },
  {
    key: "report_collectors_currentUser_comments",
    path: "/report/collectors/comments",
    component: lazy(() => import("@/views/reports/collectors/comments")),
    authority: [],
    meta: {
      header: "report.collectorReports.comments",
      headerContainer: true,
    },
  },
  {
    key: "report_collectors_currentUser_nextCalls",
    path: "/report/collectors/nextCalls",
    component: lazy(() => import("@/views/reports/collectors/nextCalls")),
    authority: [],
    meta: {
      header: "report.collectorReports.nextCalls",
      headerContainer: true,
    },
  },

  {
    key: "settings_general_archiveReasons",
    path: "/settings/general/archiveReasons",
    component: lazy(() => import("@/views/settings/general/archiveReasons")),
    authority: [],
    meta: {
      header: "settings.general.archiveReasons.archiveReasons",
      headerContainer: true,
    },
  },

  {
    key: "settings_general_archiveReasons_add",
    path: "/settings/general/archiveReasons/new",
    component: lazy(
      () => import("@/views/settings/general/archiveReasons/new")
    ),
    authority: [],
    meta: {
      header: "settings.general.archiveReasons.new",
      headerContainer: true,
    },
  },

  {
    key: "settings_general_archiveReasons_edit",
    path: "/settings/general/archiveReasons/edit",
    component: lazy(
      () => import("@/views/settings/general/archiveReasons/edit")
    ),
    authority: [],
    meta: {
      header: "settings.general.archiveReasons.edit",
      headerContainer: true,
    },
  },
  //delinquency_reasons
{
    key: "settings_general_delinquencyReasons",
    path: "/settings/general/delinquencyReasons",
    component: lazy(() => import("@/views/settings/general/delinquencyReasons")),
    authority: [],
    meta: {
      header: "settings.general.delinquencyReasons.delinquencyReasons",
      headerContainer: true,
    },
  },
  {
    key: "settings_general_delinquencyReasons_add",
    path: "/settings/general/delinquencyReasons/new",
    component: lazy(() => import("@/views/settings/general/delinquencyReasons/new")),
    authority: [],
    meta: {
      header: "settings.general.delinquencyReasons.new",
      headerContainer: true,
    },
  },
  {
    key: "settings_general_delinquencyReasons_edit",
    path: "/settings/general/delinquencyReasons/edit",
    component: lazy(() => import("@/views/settings/general/delinquencyReasons/edit")),
    authority: [],
    meta: {
      header: "settings.general.delinquencyReasons.edit",
      headerContainer: true,
    },
  },

  {
    key: "settings_general_collectionMethods",
    path: "/settings/general/collectionMethods",
    component: lazy(() => import("@/views/settings/general/collectionMethods")),
    authority: [],
    meta: {
      header: "settings.general.collectionMethods.collectionMethods",
      headerContainer: true,
    },
  },

  {
    key: "settings_general_collectionMethods_add",
    path: "/settings/general/collectionMethods/new",
    component: lazy(
      () => import("@/views/settings/general/collectionMethods/new")
    ),
    authority: [],
    meta: {
      header: "settings.general.collectionMethods.new",
      headerContainer: true,
    },
  },

  {
    key: "settings_general_collectionMethods_edit",
    path: "/settings/general/collectionMethods/edit",
    component: lazy(
      () => import("@/views/settings/general/collectionMethods/edit")
    ),
    authority: [],
    meta: {
      header: "settings.general.collectionMethods.edit",
      headerContainer: true,
    },
  },

  {
    key: "settings_general_nationalities",
    path: "/settings/general/nationalities",
    component: lazy(() => import("@/views/settings/general/nationalities")),
    authority: [],
    meta: {
      header: "settings.general.nationalities.nationalities",
      headerContainer: true,
    },
  },

  {
    key: "settings_general_nationalities_add",
    path: "/settings/general/nationalities/new",
    component: lazy(() => import("@/views/settings/general/nationalities/new")),
    authority: [],
    meta: {
      header: "settings.general.nationalities.new",
      headerContainer: true,
    },
  },

  {
    key: "settings_general_nationalities_edit",
    path: "/settings/general/nationalities/edit",
    component: lazy(
      () => import("@/views/settings/general/nationalities/edit")
    ),
    authority: [],
    meta: {
      header: "settings.general.nationalities.edit",
      headerContainer: true,
    },
  },

  {
    key: "settings_general_updateSide",
    path: "/settings/general/updateSide",
    component: lazy(() => import("@/views/settings/general/updateSide")),
    authority: [],
    meta: {
      header: "settings.general.updateSide.updateSide",
      headerContainer: true,
    },
  },
  {
    key: "settings_general_updateSide_add",
    path: "/settings/general/updateSide/new",
    component: lazy(() => import("@/views/settings/general/updateSide/new")),
    authority: [],
    meta: {
      header: "settings.general.updateSide.new",
      headerContainer: true,
    },
  },

  {
    key: "settings_general_updateSide_edit",
    path: "/settings/general/updateSide/edit",
    component: lazy(() => import("@/views/settings/general/updateSide/edit")),
    authority: [],
    meta: {
      header: "settings.general.updateSide.edit",
      headerContainer: true,
    },
  },

  {
    key: "settings_comments_status",
    path: "/settings/comments/commentStatus",
    component: lazy(() => import("@/views/settings/comments/commentStatus")),
    authority: [],
    meta: {
      header: "settings.comments.commentStatus.commentStatus",
      headerContainer: true,
    },
  },

  {
    key: "settings_comments_status_add",
    path: "/settings/comments/commentStatus/new",
    component: lazy(
      () => import("@/views/settings/comments/commentStatus/new")
    ),
    authority: [],
    meta: {
      header: "settings.comments.commentStatus.new",
      headerContainer: true,
    },
  },

  {
    key: "settings_comments_status_edit",
    path: "/settings/comments/commentStatus/edit",
    component: lazy(
      () => import("@/views/settings/comments/commentStatus/edit")
    ),
    authority: [],
    meta: {
      header: "settings.comments.commentStatus.edit",
      headerContainer: true,
    },
  },

  {
    key: "roles",
    path: "/permissions",
    component: lazy(() => import("@/views/permissions")),
    authority: [],
    meta: {
      header: "permission.title",
      headerContainer: true,
    },
  },

  {
    key: "roles_add",
    path: "/permissions/new",
    component: lazy(() => import("@/views/permissions/new")),
    authority: [],
    meta: {
      header: "permission.new",
      headerContainer: true,
    },
  },

  {
    key: "roles_edit",
    path: "/permissions/edit",
    component: lazy(() => import("@/views/permissions/edit")),
    authority: [],
    meta: {
      header: "permission.edit",
      headerContainer: true,
    },
  },

  /* ── Phase 1 screens. The route `key` is the permission key that
   *    AuthorityGuard checks, and matches the nav item key. ── */
  {
    key: "attention_queue",
    path: "/attention",
    component: lazy(() => import("@/views/phase1/attention")),
    authority: [],
  },
  {
    key: "follow_ups",
    path: "/follow-ups",
    component: lazy(() => import("@/views/phase1/followUps")),
    authority: [],
  },
  {
    key: "promises",
    path: "/promises",
    component: lazy(() => import("@/views/phase1/promises")),
    authority: [],
  },
  {
    key: "payments_approve",
    path: "/payment-approvals",
    component: lazy(() => import("@/views/phase1/paymentApprovals")),
    authority: [],
  },
  {
    key: "case_corrections_review",
    path: "/corrections",
    component: lazy(() => import("@/views/phase1/corrections")),
    authority: [],
  },

  /* ── Phase 2: reviewed bulk jobs. `bulk_jobs` is derived (any bulk kind key
   *    or bulk_jobs_all); each wizard also checks the kind's own `allowed`. ── */
  {
    key: "bulk_jobs",
    path: "/jobs",
    component: lazy(() => import("@/views/phase2/jobs")),
    authority: [],
  },
  {
    key: "bulk_jobs",
    path: "/jobs/new/:kind",
    component: lazy(() => import("@/views/phase2/bulk/NewJobPage")),
    authority: [],
  },
  {
    key: "bulk_jobs",
    path: "/jobs/run/:id",
    component: lazy(() => import("@/views/phase2/bulk/RunJobPage")),
    authority: [],
  },

  /* ── Phase 3: requests, repayment plans, team targets. Each key is derived
   *    (utils/v2/derivedPermissions): any key the screen's list endpoint accepts. ── */
  {
    key: "requests_any",
    path: "/requests",
    component: lazy(() => import("@/views/phase3/requests")),
    authority: [],
  },
  {
    key: "plans_any",
    path: "/plans",
    component: lazy(() => import("@/views/phase3/plans")),
    authority: [],
  },
  {
    key: "team_any",
    path: "/team",
    component: lazy(() => import("@/views/phase3/team")),
    authority: [],
  },

  /* ── Phase 4: the reports hub and the generic report viewer. `reports_any`
   *    is derived (any report's key, mirroring GET /v2/reports); every report
   *    call re-checks that report's own key. The legacy report screens stay. ── */
  {
    key: "reports_any",
    path: "/reports",
    component: lazy(() => import("@/views/phase4/hub")),
    authority: [],
  },
  {
    key: "reports_any",
    path: "/reports/:id",
    component: lazy(() => import("@/views/phase4/viewer")),
    authority: [],
  },

  /* ── Phase 5: calling. The route key is the permission key (derived keys in
   *    utils/v2/derivedPermissions: calling_any, campaigns_any). ── */
  {
    key: "calls",
    path: "/calls",
    component: lazy(() => import("@/views/phase5/calls")),
    authority: [],
  },
  {
    key: "calling_any",
    path: "/calling",
    component: lazy(() => import("@/views/phase5/calling")),
    authority: [],
  },
  {
    key: "campaigns_any",
    path: "/campaigns",
    component: lazy(() => import("@/views/phase5/campaigns")),
    authority: [],
  },
  {
    key: "campaigns_any",
    path: "/campaigns/:id",
    component: lazy(() => import("@/views/phase5/campaigns/CampaignDetail")),
    authority: [],
  },
  {
    key: "call_review",
    path: "/call-review",
    component: lazy(() => import("@/views/phase5/review")),
    authority: [],
  },
  {
    key: "calling_admin",
    path: "/calling-admin",
    component: lazy(() => import("@/views/phase5/admin")),
    authority: [],
  },

  {
    key: "accessDenied",
    path: "/access-denied",
    component: lazy(() => import("@/views/pages/AccessDenied")),
    authority: [],
  },
];
