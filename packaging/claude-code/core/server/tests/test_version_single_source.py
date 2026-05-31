# Verifies the package version is single-sourced from the plugin manifest
# (.claude-plugin/plugin.json) rather than hand-maintained as a second hardcoded
# literal in pyproject.toml. plugin.json is the only version the Claude Code
# marketplace consumes; pyproject's value is otherwise unread (no PyPI publish,
# no runtime reader), so it must DERIVE from plugin.json, not duplicate it.
import importlib.metadata
import json
import tomllib
from pathlib import Path

# test file: <claude-code>/core/server/tests/test_version_single_source.py
_SERVER_DIR = Path(__file__).resolve().parent.parent
_PYPROJECT = _SERVER_DIR / "pyproject.toml"
_PLUGIN_JSON = _SERVER_DIR.parents[1] / ".claude-plugin" / "plugin.json"


def _pyproject() -> dict:
    return tomllib.loads(_PYPROJECT.read_text())


def _plugin_version() -> str:
    return json.loads(_PLUGIN_JSON.read_text())["version"]


def test_plugin_json_is_the_canonical_version_source():
    # The manifest the marketplace reads must carry a concrete version.
    assert _PLUGIN_JSON.is_file()
    v = _plugin_version()
    assert v and v != "unknown"


def test_pyproject_does_not_hardcode_a_second_version():
    # No static [project].version literal — that is the duplicated number we
    # are removing. The field must be declared dynamic instead.
    project = _pyproject()["project"]
    assert "version" not in project, (
        "pyproject [project].version is hardcoded; it must be derived "
        "from plugin.json (declare it under [project].dynamic)."
    )
    assert "version" in project.get("dynamic", []), (
        "expected 'version' in [project].dynamic"
    )


def test_pyproject_sources_version_from_plugin_json():
    # The hatch version hook must point at the plugin manifest, so the two
    # cannot drift: one edit to plugin.json moves both.
    hatch_version = _pyproject().get("tool", {}).get("hatch", {}).get("version", {})
    assert hatch_version, "missing [tool.hatch.version] derivation config"
    path = hatch_version.get("path")
    assert path, "[tool.hatch.version].path must be set"
    resolved = (_SERVER_DIR / path).resolve()
    assert resolved == _PLUGIN_JSON.resolve(), (
        f"[tool.hatch.version].path resolves to {resolved}, "
        f"expected the plugin manifest {_PLUGIN_JSON.resolve()}"
    )


def test_installed_version_equals_plugin_json():
    # The end-to-end invariant: whatever the build backend produced for the
    # installed distribution must equal plugin.json. Guards that the derivation
    # is wired correctly (and stays wired) under the editable install.
    # Assumes the package is installed in the current interpreter — always true
    # under the project's `core/server/.venv` (editable install); a bare
    # interpreter without it raises PackageNotFoundError rather than skipping.
    installed = importlib.metadata.version("dpd-mcp-server")
    assert installed == _plugin_version()
