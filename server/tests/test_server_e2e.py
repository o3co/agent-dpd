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
    data_dir = tmp_path / "prgp-data"
    env = {
        "PRGP_DATA_DIR": str(data_dir),
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path),
    }

    proc = subprocess.Popen(
        [sys.executable, "-m", "prgp_mcp_server"],
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
        return json.loads(line)

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

    # The server does not proactively send roots/list — it calls list_roots()
    # lazily on the first tool invocation. Go straight to start_session.

    # Call start_session.
    send({
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "start_session", "arguments": {}},
    })
    while True:
        m = recv()
        if m.get("id") == 2:
            session_payload = json.loads(
                m["result"]["content"][0]["text"]
            )
            session_id = session_payload["session_id"]
            break
        if m.get("method") == "roots/list":
            send({
                "jsonrpc": "2.0", "id": m["id"],
                "result": {"roots": [{
                    "uri": f"file://{agent_root}", "name": "fake"
                }]},
            })

    assert session_id.startswith("ses_")

    # Spawn a root.
    send({
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {"name": "spawn_root", "arguments": {
            "session_id": session_id, "topic": "T", "reason": "R",
        }},
    })
    while True:
        m = recv()
        if m.get("id") == 3:
            root_id = json.loads(m["result"]["content"][0]["text"])["root_id"]
            break
        if m.get("method") == "roots/list":
            send({
                "jsonrpc": "2.0", "id": m["id"],
                "result": {"roots": [{
                    "uri": f"file://{agent_root}", "name": "fake"
                }]},
            })

    assert root_id.startswith("root_")

    proc.terminate()
    proc.wait(timeout=5)

    # Side-effect: sqlite was created under PRGP_DATA_DIR.
    expected = data_dir / str(agent_root).replace("/", "-") / "graph.sqlite"
    assert expected.exists()
