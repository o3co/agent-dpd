"""End-to-end smoke test: spawn the server over stdio, run a tool chain."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.timeout(15)
def test_full_chain_through_stdio(tmp_path: Path) -> None:
    # Fake agent scope: directory with Makefile + AGENTS.md markers.
    agent_root = tmp_path / "fake-agent"
    agent_root.mkdir()
    (agent_root / "Makefile").write_text("# marker")
    (agent_root / "AGENTS.md").write_text("# marker")

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
        root_id = root_payload["root_id"]
        assert root_id.startswith("root_")

        walk_payload = call_tool(4, "walk_subtree", {
            "session_id": session_id, "root_id": root_id,
        })
        # Newly spawned root has no children yet.
        assert walk_payload == {"nodes": []}

        # Side-effect: sqlite was created under DPD_DATA_DIR.
        expected = data_dir / str(agent_root).replace("/", "-") / "graph.sqlite"
        assert expected.exists()
    finally:
        proc.terminate()
        proc.wait(timeout=5)
