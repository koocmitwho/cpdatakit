"""Only maintained installation entry points participate in offline release checks."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ENTRIES = (
    "README.md",
    "README.zh-CN.md",
    "docs/quickstart.md",
    "docs/maintenance.md",
    "docs/roadmap.md",
    "docs/post-v07-workflows.md",
)


@pytest.fixture
def checker(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "current_release_check", ROOT / "scripts/check_release.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "ROOT", tmp_path)
    for name, text in {
        "pyproject.toml": 'version = "0.8.1"',
        "src/cpdatakit/_version.py": '__version__ = "0.8.1"',
        "CITATION.cff": "version: 0.8.1",
        "CHANGELOG.md": "## [0.8.1] - 2026-09-14",
        ".github/release-notes/v0.8.1.md": "Release notes",
        **dict.fromkeys(ENTRIES, 'python -m pip install "cpdatakit==0.8.1"'),
    }.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return module


@pytest.mark.parametrize("entry", ENTRIES)
@pytest.mark.parametrize(
    "installation",
    [
        'python -m pip install "cpdatakit==0.8.0"',
        'python -m pip install "https://github.com/koocmitwho/cpdatakit/releases/download/v0.8.0/cpdatakit-0.8.1-py3-none-any.whl"',
        'python -m pip install "https://github.com/koocmitwho/cpdatakit/releases/download/v0.8.1/cpdatakit-0.8.0-py3-none-any.whl"',
    ],
)
def test_stale_current_installation_is_rejected(checker, entry, installation):
    (checker.ROOT / entry).write_text(installation, encoding="utf-8")
    with pytest.raises(ValueError, match=entry):
        checker.verify_release("v0.8.1")


def test_historical_versions_and_unpublished_candidate_are_allowed(checker):
    (checker.ROOT / "docs/old-release.md").write_text('pip install "cpdatakit==0.6.0"')
    roadmap = checker.ROOT / "docs/roadmap.md"
    roadmap.write_text(roadmap.read_text() + "\n## v0.8.0 (released 2026-09-08)\n")
    assert checker.verify_release("v0.8.1") == "0.8.1"
