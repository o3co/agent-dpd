"""Tests for agent-scope resolution from MCP roots."""

from __future__ import annotations

from pathlib import Path

import pytest

from dpd_mcp_server.scope import (
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


def test_resolve_walks_up_to_dpdrc_marker(tmp_path: Path, monkeypatch) -> None:
    """`.dpdrc` at an ancestor directory anchors the agent scope."""
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))

    agent_root = fake_home / "any-project"
    (agent_root / "subdir").mkdir(parents=True)
    (agent_root / ".dpdrc").write_text("scope=my-project\n")

    encoded = resolve_agent_scope([f"file://{agent_root / 'subdir'}"])

    assert encoded == encode_agent_scope_path(agent_root)


def test_resolve_empty_dpdrc_is_valid_marker(tmp_path: Path, monkeypatch) -> None:
    """An empty `.dpdrc` (no scope= line) still marks the agent scope root."""
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))

    agent_root = fake_home / "loose-project"
    (agent_root / "nested").mkdir(parents=True)
    (agent_root / ".dpdrc").write_text("")  # empty file

    encoded = resolve_agent_scope([f"file://{agent_root / 'nested'}"])

    assert encoded == encode_agent_scope_path(agent_root)


def test_resolve_returns_root_as_is_when_no_marker(tmp_path: Path, monkeypatch) -> None:
    """No `.dpdrc` anywhere up the tree → encode roots[0] as-is."""
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))

    workspace = fake_home / "loose"
    workspace.mkdir()

    encoded = resolve_agent_scope([f"file://{workspace}"])

    assert encoded == encode_agent_scope_path(workspace)


def test_resolve_returns_root_when_marker_is_at_root_itself(tmp_path: Path, monkeypatch) -> None:
    """`.dpdrc` directly at the supplied root resolves to that root."""
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))

    agent_root = fake_home / "agent-scope"
    agent_root.mkdir()
    (agent_root / ".dpdrc").write_text("scope=my-scope\n")

    encoded = resolve_agent_scope([f"file://{agent_root}"])

    assert encoded == encode_agent_scope_path(agent_root)


def test_resolve_refuses_dpdrc_at_home(tmp_path: Path, monkeypatch) -> None:
    """A `.dpdrc` directly at $HOME is refused (bounded walk-up)."""
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()
    (fake_home / ".dpdrc").write_text("scope=global\n")
    monkeypatch.setenv("HOME", str(fake_home))

    project = fake_home / "some-project"
    project.mkdir()

    encoded = resolve_agent_scope([f"file://{project}"])

    # Marker at $HOME refused → fallback to roots[0].
    assert encoded == encode_agent_scope_path(project)


def test_resolve_accepts_dpdrc_below_home(tmp_path: Path, monkeypatch) -> None:
    """A `.dpdrc` at any directory strictly below $HOME is accepted."""
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))

    workspace = fake_home / "workspace"
    (workspace / "sub").mkdir(parents=True)
    (workspace / ".dpdrc").write_text("scope=workspace-scope\n")

    encoded = resolve_agent_scope([f"file://{workspace / 'sub'}"])

    assert encoded == encode_agent_scope_path(workspace)


def test_resolve_picks_innermost_dpdrc(tmp_path: Path, monkeypatch) -> None:
    """When `.dpdrc` exists at multiple ancestor levels, the closest wins."""
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))

    outer = fake_home / "outer"
    inner = outer / "inner"
    (inner / "leaf").mkdir(parents=True)
    (outer / ".dpdrc").write_text("scope=outer\n")
    (inner / ".dpdrc").write_text("scope=inner\n")

    encoded = resolve_agent_scope([f"file://{inner / 'leaf'}"])

    assert encoded == encode_agent_scope_path(inner)
