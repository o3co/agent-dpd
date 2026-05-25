# Lives under core/server/tests/ rather than a repo-root tests/ because core/server/.venv is the
# only venv and core/server/pyproject.toml is the only pytest config — keeping it here
# means `make test` covers both the MCP server and the installer in one pass.

"""Tests for install.sh's skill-linking logic."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
INSTALL_SH = REPO_ROOT / "install.sh"


def _make_fixture_repo(tmp_path: Path) -> Path:
    """Stage a minimal repo with core/skills/ containing sub-skill dirs (uniform layout).

    Layout:
      repo/core/skills/dpd/SKILL.md              -> main /dpd skill (now a subdir like any other)
      repo/core/skills/dpd-foo/SKILL.md          -> sub-skill
      repo/core/skills/dpd-bar/SKILL.md          -> sub-skill
      repo/core/skills/dpd-baz/SKILL.md          -> sub-skill
      repo/core/skills/not-a-skill/README.md     -> NOT a skill (no SKILL.md) — must be skipped
    """
    repo = tmp_path / "repo"
    skills = repo / "core" / "skills"
    skills.mkdir(parents=True)
    dpd_sub = skills / "dpd"
    dpd_sub.mkdir()
    (dpd_sub / "SKILL.md").write_text("# main dpd skill\n")
    for name in ("dpd-foo", "dpd-bar", "dpd-baz"):
        sub = skills / name
        sub.mkdir()
        (sub / "SKILL.md").write_text(f"# {name}\n")
    decoy = skills / "not-a-skill"
    decoy.mkdir()
    (decoy / "README.md").write_text("not a skill\n")
    return repo


def _run_link_skills(repo: Path, skills_home: Path) -> subprocess.CompletedProcess:
    """Source install.sh and invoke link_skills(repo, skills_home)."""
    script = f'source "{INSTALL_SH}" && link_skills "{repo}" "{skills_home}"'
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env={**os.environ, "DPD_NO_REGISTER": "1"},  # belt + suspenders if guard ever slips
    )


def test_link_skills_creates_main_and_sub_symlinks(tmp_path: Path) -> None:
    repo = _make_fixture_repo(tmp_path)
    skills_home = tmp_path / "skills_home"

    result = _run_link_skills(repo, skills_home)

    assert result.returncode == 0, f"stderr: {result.stderr}\nstdout: {result.stdout}"

    main = skills_home / "dpd"
    assert main.is_symlink()
    assert os.readlink(main) == str(repo / "core" / "skills" / "dpd")

    for name in ("dpd-foo", "dpd-bar", "dpd-baz"):
        link = skills_home / name
        assert link.is_symlink(), f"missing symlink for {name}"
        assert os.readlink(link) == str(repo / "core" / "skills" / name)

    assert not (skills_home / "not-a-skill").exists(), "subdir without SKILL.md must be skipped"


def test_link_skills_replaces_existing_symlink(tmp_path: Path) -> None:
    repo = _make_fixture_repo(tmp_path)
    skills_home = tmp_path / "skills_home"
    skills_home.mkdir()

    stale_target = tmp_path / "stale"
    stale_target.mkdir()
    (skills_home / "dpd").symlink_to(stale_target)
    assert os.readlink(skills_home / "dpd") == str(stale_target)

    result = _run_link_skills(repo, skills_home)

    assert result.returncode == 0, f"stderr: {result.stderr}\nstdout: {result.stdout}"
    assert os.readlink(skills_home / "dpd") == str(repo / "core" / "skills" / "dpd")


def test_link_skills_errors_on_existing_directory(tmp_path: Path) -> None:
    repo = _make_fixture_repo(tmp_path)
    skills_home = tmp_path / "skills_home"
    skills_home.mkdir()

    collision = skills_home / "dpd"
    collision.mkdir()
    (collision / "marker.txt").write_text("user content\n")

    result = _run_link_skills(repo, skills_home)

    assert result.returncode != 0, "link_skills must refuse to overwrite a real directory"
    combined = result.stderr + result.stdout
    assert "refuses to overwrite" in combined.lower(), f"expected refusal message, got: {combined}"
    assert (collision / "marker.txt").exists(), "must not delete user content"


def test_link_skills_rejects_repo_without_main_skill(tmp_path: Path) -> None:
    """If core/skills/dpd/SKILL.md is missing, link_skills must refuse rather than create a dangling symlink."""
    bad_repo = tmp_path / "bad_repo"
    (bad_repo / "core" / "skills").mkdir(parents=True)
    skills_home = tmp_path / "skills_home"

    result = _run_link_skills(bad_repo, skills_home)

    assert result.returncode != 0, "link_skills must fail when the source skill tree is invalid"
    combined = result.stderr + result.stdout
    assert "SKILL.md" in combined, f"expected source-validation error, got: {combined}"
    assert not (skills_home / "dpd").exists(), "must not create dangling symlink"


def test_install_sh_main_guarded_when_sourced(tmp_path: Path) -> None:
    """Sourcing install.sh must not run main() (no clone, no venv, no claude mcp add)."""
    sentinel = tmp_path / "main_ran"
    result = subprocess.run(
        ["bash", "-c", f'source "{INSTALL_SH}" && echo sourced'],
        capture_output=True,
        text=True,
        env={**os.environ, "DPD_INSTALL_DIR": str(sentinel)},
    )
    assert result.returncode == 0, f"sourcing failed: {result.stderr}"
    assert "sourced" in result.stdout
    assert not sentinel.exists(), "main() must not run on source"


# ---------------------------------------------------------------------------
# Cursor mcp.json patching
# ---------------------------------------------------------------------------

import json
import shutil


@pytest.fixture
def fake_cursor_home(tmp_path):
    """Provide a tmp dir to use as the Cursor home for tests."""
    cursor_home = tmp_path / ".cursor"
    cursor_home.mkdir()
    return cursor_home


def test_patch_cursor_mcp_adds_dpd_server(fake_cursor_home):
    """patch_cursor_mcp adds dpd-mcp-server entry to ~/.cursor/mcp.json."""
    mcp_json = fake_cursor_home / "mcp.json"

    result = subprocess.run(
        ["bash", "-c", f"""
        source {INSTALL_SH}
        patch_cursor_mcp "{mcp_json}" "/fake/path/to/dpd-mcp-server"
        """],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert mcp_json.exists()

    data = json.loads(mcp_json.read_text())
    assert "mcpServers" in data
    assert "dpd-mcp-server" in data["mcpServers"]
    assert data["mcpServers"]["dpd-mcp-server"]["command"] == "/fake/path/to/dpd-mcp-server"


def test_patch_cursor_mcp_preserves_existing_entries(fake_cursor_home):
    """patch_cursor_mcp preserves user's other MCP server entries."""
    mcp_json = fake_cursor_home / "mcp.json"
    mcp_json.write_text(json.dumps({
        "mcpServers": {
            "other-server": {"command": "/other/path", "args": ["-x"]}
        }
    }))

    result = subprocess.run(
        ["bash", "-c", f"""
        source {INSTALL_SH}
        patch_cursor_mcp "{mcp_json}" "/fake/dpd"
        """],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"

    data = json.loads(mcp_json.read_text())
    assert "other-server" in data["mcpServers"]
    assert "dpd-mcp-server" in data["mcpServers"]
    assert data["mcpServers"]["other-server"]["command"] == "/other/path"
