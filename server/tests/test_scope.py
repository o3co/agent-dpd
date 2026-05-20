"""Tests for agent-scope resolution from MCP roots."""

from __future__ import annotations

from pathlib import Path

import pytest

from prgp_mcp_server.scope import (
    AgentScopeResolutionError,
    encode_agent_scope_path,
    resolve_agent_scope,
)


def test_resolve_raises_when_no_roots() -> None:
    with pytest.raises(AgentScopeResolutionError):
        resolve_agent_scope([])


def test_encode_agent_scope_path_replaces_slashes_with_dashes() -> None:
    result = encode_agent_scope_path(Path("/Volumes/Workspace/scopes/mcp"))
    assert result == "-Volumes-Workspace-scopes-mcp"


def test_encode_agent_scope_path_rejects_relative_path() -> None:
    with pytest.raises(ValueError):
        encode_agent_scope_path(Path("scopes/mcp"))


def test_resolve_walks_up_to_agent_scope_marker(tmp_path: Path, monkeypatch) -> None:
    # Place everything below a fake home so the bounded walk-up accepts it.
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))

    agent_root = fake_home / "agent-scope"
    (agent_root / "scopes/sub").mkdir(parents=True)
    (agent_root / "Makefile").write_text("# marker")
    (agent_root / "AGENTS.md").write_text("# marker")

    encoded = resolve_agent_scope([f"file://{agent_root / 'scopes/sub'}"])

    assert encoded == encode_agent_scope_path(agent_root)


def test_resolve_returns_root_as_is_when_no_marker(tmp_path: Path, monkeypatch) -> None:
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))

    workspace = fake_home / "loose"
    workspace.mkdir()

    encoded = resolve_agent_scope([f"file://{workspace}"])

    assert encoded == encode_agent_scope_path(workspace)


def test_resolve_returns_root_when_marker_is_at_root_itself(tmp_path: Path, monkeypatch) -> None:
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))

    agent_root = fake_home / "agent-scope"
    agent_root.mkdir()
    (agent_root / "Makefile").write_text("# marker")
    (agent_root / "AGENTS.md").write_text("# marker")

    encoded = resolve_agent_scope([f"file://{agent_root}"])

    assert encoded == encode_agent_scope_path(agent_root)


def test_resolve_refuses_marker_at_home(tmp_path: Path, monkeypatch) -> None:
    # Simulate ~/Makefile + ~/AGENTS.md.
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()
    (fake_home / "Makefile").write_text("# marker")
    (fake_home / "AGENTS.md").write_text("# marker")
    monkeypatch.setenv("HOME", str(fake_home))

    project = fake_home / "some-project"
    project.mkdir()

    # When the only marker is at $HOME, refuse it and fall back to roots[0].
    encoded = resolve_agent_scope([f"file://{project}"])

    assert encoded == encode_agent_scope_path(project)


def test_resolve_accepts_marker_below_home(tmp_path: Path, monkeypatch) -> None:
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))

    workspace = fake_home / "workspace"
    (workspace / "sub").mkdir(parents=True)
    (workspace / "Makefile").write_text("# marker")
    (workspace / "AGENTS.md").write_text("# marker")

    encoded = resolve_agent_scope([f"file://{workspace / 'sub'}"])

    assert encoded == encode_agent_scope_path(workspace)
