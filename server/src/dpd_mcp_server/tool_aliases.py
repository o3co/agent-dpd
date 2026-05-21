"""Transitional aliases for MCP tool renames.

Convention (spec docs/dpd-phase-2.7-draft.md §4):
- Keys = old tool names, values = new tool names.
- Aliases are exposed in list_tools with "[DEPRECATED: use <new>
  instead]" prefix on the description.
- call_tool forwards alias calls to the new handler and emits a
  logger.warning.
- Retention: 1 minor release cycle (OSS 後) or 14 days minimum
  (OSS 前) per spec §4.2.
- Scope: rename only. Signature changes are out of scope.

Currently empty — Phase 2.7 lands the machinery without any pending
renames.
"""

LEGACY_ALIASES: dict[str, str] = {
    # "old_name": "new_name",
}
