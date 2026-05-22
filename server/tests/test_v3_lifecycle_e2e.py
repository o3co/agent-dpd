"""End-to-end v0.3 lifecycle: Pool → DPD subgraph → mark_reached → dump_persist → delete."""
from __future__ import annotations

from dpd_mcp_server.storage import Storage
from dpd_mcp_server import tools


def test_full_v3_lifecycle(tmp_db_path: str) -> None:
    storage = Storage.open(tmp_db_path)

    # 1. Pool add
    added = tools.pool_add(
        storage,
        scope="dpd",
        arguments={"text": "evidence X observed"},
        now="2026-05-22T00:00:00Z",
    )
    pool_id_ = added["pool_item"]["id"]

    # 2. Set up subgraph: Start + End under a legacy session/root
    storage.insert_session(session_id="ses_1", scope="dpd", label=None,
                           now="2026-05-22T00:00:00Z")
    storage.insert_root(root_id="r_legacy", session_id="ses_1", topic="t",
                        now="2026-05-22T00:00:00Z")

    _ids = iter(["n_start", "n_end"])
    start_res = tools.add_node(
        storage=storage,
        arguments={"session_id": "ses_1", "parent_id": "r_legacy",
                   "type": "start", "text": "S"},
        now="2026-05-22T00:00:00Z",
        new_id=lambda p: next(_ids),
    )
    sid = start_res["node"]["id"]

    end_res = tools.add_node(
        storage=storage,
        arguments={"session_id": "ses_1", "parent_id": sid,
                   "type": "end", "text": "E",
                   "paired_for": sid,
                   "achievement_conditions": "done when X"},
        now="2026-05-22T00:00:00Z",
        new_id=lambda p: next(_ids),
    )
    eid = end_res["node"]["id"]

    # 3. Pool elevate (pool item becomes child of End)
    elevated = tools.pool_elevate(
        storage,
        scope="dpd",
        arguments={"pool_id": pool_id_, "target_end_node_id": eid,
                   "type": "evidence", "session_id": "ses_1"},
        now="2026-05-22T01:00:00Z",
    )
    assert elevated["elevated_node"]["type"] == "evidence"
    # pool list (active) is now empty
    assert tools.pool_list(
        storage,
        scope="dpd",
        arguments={"active_only": True},
        now="2026-05-22T01:00:00Z",
    )["items"] == []

    # 4. mark_reached → subgraph closes
    tools.mark_reached(
        storage,
        arguments={"session_id": "ses_1", "end_node_id": eid},
        now="2026-05-22T02:00:00Z",
    )
    end = storage.get_node(session_id="ses_1", node_id=eid)
    assert end["state"] == "closed"

    # 5. dump_persist → deletable
    tools.dump_persist(
        storage,
        arguments={"session_id": "ses_1", "start_node_id": sid,
                   "destination": "/tmp/dump.md"},
        now="2026-05-22T03:00:00Z",
    )
    start = storage.get_node(session_id="ses_1", node_id=sid)
    assert start["state"] == "deletable"

    # 6. delete → physical removal
    tools.delete(
        storage,
        arguments={"session_id": "ses_1", "start_node_id": sid},
        now="2026-05-22T04:00:00Z",
    )
    assert storage.get_node(session_id="ses_1", node_id=sid) is None
    assert storage.get_node(session_id="ses_1", node_id=eid) is None
