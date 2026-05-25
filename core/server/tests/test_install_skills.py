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
    """Stage a minimal repo with skill/ containing sub-skill dirs (uniform layout).

    Layout:
      repo/skill/dpd/SKILL.md              -> main /dpd skill (now a subdir like any other)
      repo/skill/dpd-foo/SKILL.md          -> sub-skill
      repo/skill/dpd-bar/SKILL.md          -> sub-skill
      repo/skill/dpd-baz/SKILL.md          -> sub-skill
      repo/skill/not-a-skill/README.md     -> NOT a skill (no SKILL.md) — must be skipped
    """
    repo = tmp_path / "repo"
    skill = repo / "skill"
    skill.mkdir(parents=True)
    dpd_sub = skill / "dpd"
    dpd_sub.mkdir()
    (dpd_sub / "SKILL.md").write_text("# main dpd skill\n")
    for name in ("dpd-foo", "dpd-bar", "dpd-baz"):
        sub = skill / name
        sub.mkdir()
        (sub / "SKILL.md").write_text(f"# {name}\n")
    decoy = skill / "not-a-skill"
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
    assert os.readlink(main) == str(repo / "skill" / "dpd")

    for name in ("dpd-foo", "dpd-bar", "dpd-baz"):
        link = skills_home / name
        assert link.is_symlink(), f"missing symlink for {name}"
        assert os.readlink(link) == str(repo / "skill" / name)

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
    assert os.readlink(skills_home / "dpd") == str(repo / "skill" / "dpd")


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
    """If skill/dpd/SKILL.md is missing, link_skills must refuse rather than create a dangling symlink."""
    bad_repo = tmp_path / "bad_repo"
    (bad_repo / "skill").mkdir(parents=True)
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
