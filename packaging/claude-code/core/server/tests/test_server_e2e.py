"""End-to-end smoke test: spawn the server over stdio, run a tool chain."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.timeout(15)
def test_full_chain_through_stdio(tmp_path: Path) -> None:
    # Fake agent scope: directory with .dpdrc marker.
    agent_root = tmp_path / "fake-agent"
    agent_root.mkdir()
    (agent_root / ".dpdrc").write_text("scope=test\n")

    # Override the data dir via env var so the test does not touch ~/.claude.
    data_dir = tmp_path / "dpd-data"
    env = {
        "DPD_DATA_DIR": str(data_dir),
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path),
    }

    proc = subprocess.Popen(
        [sys.executable, "-m", "dpd_mcp_server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        cwd=str(agent_root),
    )

    def send(obj: dict) -> None:
        assert proc.stdin is not None
        proc.stdin.write((json.dumps(obj) + "\n").encode())
        proc.stdin.flush()

    def recv() -> dict:
        assert proc.stdout is not None
        line = proc.stdout.readline().decode()
        if not line:
            stderr = proc.stderr.read().decode() if proc.stderr else ""
            raise RuntimeError(
                f"Server process closed stdout (likely crashed). stderr:\n{stderr}"
            )
        return json.loads(line)

    def respond_roots(request_id: int) -> None:
        send({
            "jsonrpc": "2.0", "id": request_id,
            "result": {"roots": [{
                "uri": f"file://{agent_root}", "name": "fake"
            }]},
        })

    def call_tool(call_id: int, name: str, arguments: dict) -> dict:
        """Send a tools/call and return its parsed result payload."""
        send({
            "jsonrpc": "2.0", "id": call_id, "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        })
        while True:
            m = recv()
            if m.get("id") == call_id and "result" in m:
                return json.loads(m["result"]["content"][0]["text"])
            if m.get("method") == "roots/list":
                respond_roots(m["id"])
            else:
                raise AssertionError(f"unexpected message: {m}")

    try:
        # MCP initialize handshake — client advertises roots capability.
        send({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {"roots": {"listChanged": False}},
                "clientInfo": {"name": "e2e-test", "version": "0"},
            },
        })
        init_resp = recv()
        assert init_resp["id"] == 1
        assert "result" in init_resp

        send({"jsonrpc": "2.0", "method": "notifications/initialized"})

        # start_session → spawn_root → walk_subtree round trip.
        session_payload = call_tool(2, "start_session", {})
        session_id = session_payload["session_id"]
        assert session_id.startswith("ses_")

        root_payload = call_tool(3, "spawn_root", {
            "session_id": session_id, "topic": "T", "reason": "R",
        })
        root_id = root_payload["root"]["id"]
        assert root_id.startswith("root_")
        assert root_payload["root"]["topic"] == "T"

        walk_payload = call_tool(4, "walk_subtree", {
            "session_id": session_id, "root_id": root_id,
        })
        # Newly spawned root has no children yet.
        assert walk_payload == {"nodes": []}

        # Skill startup flow: list_sessions then get_session_state for resume.
        list_payload = call_tool(5, "list_sessions", {})
        assert [s["id"] for s in list_payload["sessions"]] == [session_id]

        state_payload = call_tool(6, "get_session_state", {
            "session_id": session_id,
        })
        assert state_payload["session"]["id"] == session_id
        assert [r["id"] for r in state_payload["active_roots"]] == [root_id]
        assert state_payload["focus_node"] is None

        # Phase 2.5 tool surface: spawn 3 hypotheses under the root, then
        # use resolve_hypothesis_branch to atomically resolve one branch.
        hyp_ids = []
        for i in range(3):
            payload = call_tool(7 + i, "add_node", {
                "session_id": session_id, "parent_id": root_id,
                "type": "hypothesis", "text": f"option {i}",
            })
            hyp_ids.append(payload["node"]["id"])

        accept_payload = call_tool(10, "resolve_hypothesis_branch", {
            "session_id": session_id, "hyp_id": hyp_ids[1],
            "decision_text": "Adopt option 1",
            "rationale_text": "Best fit",
        })
        assert accept_payload["hyp_id"] == hyp_ids[1]
        assert set(accept_payload["closed_siblings"]) == {hyp_ids[0], hyp_ids[2]}
        assert accept_payload["decision_id"].startswith("node_")
        assert accept_payload["rationale_id"].startswith("node_")

        # set_focus then read state back to confirm round-trip.
        call_tool(11, "set_focus", {
            "session_id": session_id, "node_id": accept_payload["decision_id"],
        })
        refreshed = call_tool(12, "get_session_state", {
            "session_id": session_id,
        })
        assert refreshed["focus_node"]["id"] == accept_payload["decision_id"]

        # list_open_nodes after closure — only nodes outside the closed branch.
        open_nodes = call_tool(13, "list_open_nodes", {
            "session_id": session_id, "root_id": root_id,
        })
        assert open_nodes["nodes"] == []

        # archive the root and confirm it disappears from list_active_roots.
        call_tool(14, "set_root_lifecycle", {
            "session_id": session_id, "root_id": root_id,
            "lifecycle": "archived",
        })
        active = call_tool(15, "list_active_roots", {
            "session_id": session_id,
        })
        assert active == {"roots": []}

        # resolve_hypothesis_branch (with rationale) auto-creates two edges:
        #   - derived_from: decision → accepted hypothesis
        #   - justifies (#57): rationale → decision (grounding)
        edges_after_resolve = call_tool(16, "list_edges", {
            "session_id": session_id,
        })
        assert len(edges_after_resolve["edges"]) == 2
        by_type = {e["type"]: e for e in edges_after_resolve["edges"]}
        assert by_type["derived_from"]["from_node"] == accept_payload["decision_id"]
        assert by_type["derived_from"]["to_node"] == hyp_ids[1]
        assert by_type["justifies"]["from_node"] == accept_payload["rationale_id"]
        assert by_type["justifies"]["to_node"] == accept_payload["decision_id"]

        # Add a manual "blocks" edge between two of the still-existing nodes.
        call_tool(17, "add_edge", {
            "session_id": session_id,
            "from_node": hyp_ids[0], "to_node": hyp_ids[2],
            "type": "blocks",
        })
        all_edges = call_tool(18, "list_edges", {"session_id": session_id})
        assert len(all_edges["edges"]) == 3
        assert {e["type"] for e in all_edges["edges"]} == {
            "derived_from", "justifies", "blocks",
        }

        # export_yaml is JSON-compatible — re-parses cleanly.
        yaml_out = call_tool(20, "export_yaml", {
            "session_id": session_id, "root_id": root_id,
        })
        parsed = json.loads(yaml_out["yaml"])
        assert parsed["session"]["id"] == session_id
        assert len(parsed["roots"]) == 1
        assert parsed["roots"][0]["id"] == root_id

        # Side-effect: sqlite was created under DPD_DATA_DIR.
        expected = data_dir / str(agent_root).replace("/", "-") / "graph.sqlite"
        assert expected.exists()
    finally:
        proc.terminate()
        proc.wait(timeout=5)


@pytest.mark.timeout(15)
def test_resolve_branch_through_stdio(tmp_path: Path) -> None:
    """Smoke-test resolve_branch: all-true verdict + decision + derived_from edges."""
    agent_root = tmp_path / "fake-agent"
    agent_root.mkdir()
    (agent_root / ".dpdrc").write_text("scope=test\n")

    data_dir = tmp_path / "dpd-data"
    env = {
        "DPD_DATA_DIR": str(data_dir),
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path),
    }

    proc = subprocess.Popen(
        [sys.executable, "-m", "dpd_mcp_server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        cwd=str(agent_root),
    )

    def send(obj: dict) -> None:
        assert proc.stdin is not None
        proc.stdin.write((json.dumps(obj) + "\n").encode())
        proc.stdin.flush()

    def recv() -> dict:
        assert proc.stdout is not None
        line = proc.stdout.readline().decode()
        if not line:
            stderr = proc.stderr.read().decode() if proc.stderr else ""
            raise RuntimeError(
                f"Server process closed stdout (likely crashed). stderr:\n{stderr}"
            )
        return json.loads(line)

    def respond_roots(request_id: int) -> None:
        send({
            "jsonrpc": "2.0", "id": request_id,
            "result": {"roots": [{
                "uri": f"file://{agent_root}", "name": "fake"
            }]},
        })

    def call_tool(call_id: int, name: str, arguments: dict) -> dict:
        send({
            "jsonrpc": "2.0", "id": call_id, "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        })
        while True:
            m = recv()
            if m.get("id") == call_id and "result" in m:
                return json.loads(m["result"]["content"][0]["text"])
            if m.get("method") == "roots/list":
                respond_roots(m["id"])
            else:
                raise AssertionError(f"unexpected message: {m}")

    try:
        send({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {"roots": {"listChanged": False}},
                "clientInfo": {"name": "e2e-test", "version": "0"},
            },
        })
        init_resp = recv()
        assert init_resp["id"] == 1
        assert "result" in init_resp
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})

        session_id = call_tool(2, "start_session", {})["session_id"]
        root_id = call_tool(3, "spawn_root", {
            "session_id": session_id, "topic": "all-true test",
        })["root"]["id"]

        hyp_ids = []
        for i in range(3):
            payload = call_tool(4 + i, "add_node", {
                "session_id": session_id, "parent_id": root_id,
                "type": "hypothesis", "text": f"option {i}",
            })
            hyp_ids.append(payload["node"]["id"])

        result = call_tool(7, "resolve_branch", {
            "session_id": session_id,
            "parent_id": root_id,
            "parent_kind": "root",
            "results": [
                {"node_id": hyp_ids[0], "closure_reason": "resolved"},
                {"node_id": hyp_ids[1], "closure_reason": "resolved"},
                {"node_id": hyp_ids[2], "closure_reason": "resolved"},
            ],
            "decision_text": "all confirmed",
            "derived_from_node_ids": hyp_ids,
        })
        assert sorted(n["id"] for n in result["closed_nodes"]) == sorted(hyp_ids)
        assert result["decision_node"] is not None
        assert result["decision_node"]["id"].startswith("node_")
        assert result["rationale_node"] is None
        assert len(result["edges_created"]) == 3

        edges = call_tool(8, "list_edges", {
            "session_id": session_id,
            "from_node": result["decision_node"]["id"],
            "type": "derived_from",
        })
        assert len(edges["edges"]) == 3
        assert sorted(e["to_node"] for e in edges["edges"]) == sorted(hyp_ids)
    finally:
        proc.terminate()
        proc.wait(timeout=5)
