"""Phase 5 human calling (P0 foundation + P1 click-to-call). See review/phase5-foundation.md.

Modules:
    windows       calling windows (Asia/Riyadh), pure functions
    dialplan      restricted extension-first dialplan generator (offline artifact)
    ami           AMI wire codec, persistent session, supervisor with PBX epoch
    agi           FastAGI authorization hook (one-shot dispatch tokens)
    originate     extension-first Originate action builder (used by P1)
    common        shared SQL/validation helpers for the calling services
    extensions    extension ownership (user_extensions) and the users.extension mirror
    settings      calling preferences and the deployment calling-window policy
    attempts      call attempts, reservations, dispatch tokens, lifecycle guard
    evidence      AMI event consumer -> call_events / call_legs / pbx_sessions
    wrapup        manual call log and the reviewed wrap-up transaction
    queries       call log reads (mine, case, scoped log, detail)
  P1 (click-to-call, review/phase5-click-to-call.md):
    rules         the dispatch rules (API, dispatcher final recheck, AGI gate recheck)
    dispatch      POST /v2/calls authorization + queue; the calling service's Dispatcher
    projection    AMI evidence -> attempt state (bridged only with bridge membership of both legs)
    reconcile     missing evidence -> uncertain; channel inventory + CDR -> ended/failed/kept
    cdr_lookup    CDR lookup by attempt id (CSV on the test PBX, MySQL on Issabel)
    live          the live-call panel view (ETag) and my active attempt
    review        supervisor lists: uncertain attempts, resolve, review flags
  P2 (progressive campaigns, review/phase5-campaigns.md):
    campaign          campaigns, roster, readiness leases, lifecycle, member/supervisor views
    campaign_pool     the pool definition (saved clients-list filter), order, exclusions, contact
    campaign_dispatch one call per ready member through dispatch.authorize(); the service loop

Nothing in this package contacts a PBX by itself. The only process that opens an AMI
session or listens for FastAGI is the `calling` service (CAWP-API/calling/).
"""
