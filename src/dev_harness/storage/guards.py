"""Namespace guard: every query must carry a project/thread scope (V11 1.6)."""

from __future__ import annotations

from dev_harness.contracts.errors import UnscopedQueryError
from dev_harness.storage.sqlite_saver import Scope


def require_scope(config: dict[str, object] | None) -> Scope:
    """Validate that a query config carries a project_id and thread_id scope.

    Raises UnscopedQueryError if either is missing. A mutant that drops the
    project_id predicate must be killed.
    """
    config = config or {}
    project_id = config.get("project_id")
    thread_id = config.get("thread_id")
    if not project_id or not thread_id:
        raise UnscopedQueryError(
            "query requires a project_id and thread_id scope",
            remediation="Pass an explicit project_id and thread_id scope to the query.",
        )
    return Scope(project_id=str(project_id), thread_id=str(thread_id))
