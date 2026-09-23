"""Reviewed bulk-job framework (Phase 2, docs/phase2-spec.md, review/phase2-framework.md).

    source -> private upload -> explicit mapping -> durable validation -> preview
           -> confirmation (partial allowed) -> bounded idempotent execution with rechecks
           -> per-case outcomes, error files, run history

Modules:
    registry   kind plug-in interface (BulkKind, MappingField, Eligibility, Outcome) + discovery
    settings   BULK_* environment settings
    storage    private file storage (uploads, artifacts)
    inspect    upload inspection (real XLSX / UTF-8 CSV, size, formula cells) + streaming readers
    sources    source validation (HTTP) and row materialisation (worker)
    engine     worker side: claim, lease recovery, validation, execution with rechecks
    artifacts  formula-safe CSV files (preview, errors, results)
    retention  periodic purge of expired files, previews and idempotency keys
    service    API side: uploads, jobs, confirm/cancel/retry/reopen, lists, downloads
    common     scope / permission / audit SQL helpers
"""
from core.bulk.registry import (BulkKind, BulkRow, CreateContext, Eligibility, ExecuteContext, KindContext,
                                MappingField, Outcome, ParamError, all_kinds, get, register)

__all__ = ["BulkKind", "BulkRow", "CreateContext", "Eligibility", "ExecuteContext", "KindContext",
           "MappingField", "Outcome", "ParamError", "all_kinds", "get", "register"]
