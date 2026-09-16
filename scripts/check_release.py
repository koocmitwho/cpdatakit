"""Fail closed when a release tag and package metadata disagree."""

from __future__ import annotations

import argparse
import re
import tarfile
import zipfile
from email.parser import BytesParser
from email.policy import default
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NUMBER = r"(?:0|[1-9][0-9]*)"
TAG_PATTERN = re.compile(rf"^v(?P<version>{NUMBER}\.{NUMBER}\.{NUMBER})$")
CURRENT_INSTALL_ENTRIES = (
    "README.md",
    "README.zh-CN.md",
    "docs/quickstart.md",
    "docs/maintenance.md",
    "docs/roadmap.md",
    "docs/post-v07-workflows.md",
)


def verify_install_entries(version: str) -> None:
    """Check maintained pins and wheel links without consulting a registry."""
    patterns = (
        r"cpdatakit(?:\[[^\]]+\])?\s*==\s*([^\s\"'`]+)",
        r"github\.com/koocmitwho/cpdatakit/releases/download/v([^/\s]+)/",
        r"cpdatakit-([^/\s]+?)-py3-none-any\.whl",
    )
    for entry in CURRENT_INSTALL_ENTRIES:
        content = (ROOT / entry).read_text(encoding="utf-8")
        for pattern in patterns:
            for declared in re.findall(pattern, content):
                if declared != version:
                    raise ValueError(
                        f"Current installation entry {entry} uses {declared}; expected {version}"
                    )


def _match_version(path: Path, pattern: str) -> str:
    match = re.search(pattern, path.read_text(encoding="utf-8"), flags=re.MULTILINE)
    if match is None:
        raise ValueError(f"Could not find version metadata in {path.relative_to(ROOT)}")
    return match.group("version")


def verify_release(tag: str) -> str:
    match = TAG_PATTERN.fullmatch(tag)
    if match is None:
        raise ValueError(f"Release ref must be a semantic-version tag such as v0.1.1, got {tag!r}")

    tag_version = match.group("version")
    versions = {
        "tag": tag_version,
        "pyproject.toml": _match_version(
            ROOT / "pyproject.toml", r'^version\s*=\s*["\'](?P<version>[^"\']+)["\']'
        ),
        "src/cpdatakit/_version.py": _match_version(
            ROOT / "src/cpdatakit/_version.py",
            r'^__version__\s*=\s*["\'](?P<version>[^"\']+)["\']',
        ),
        "CITATION.cff": _match_version(
            ROOT / "CITATION.cff", r"^version:\s*[\"']?(?P<version>[^\s\"']+)"
        ),
    }
    if len(set(versions.values())) != 1:
        details = ", ".join(f"{source}={version}" for source, version in versions.items())
        raise ValueError(f"Release versions disagree: {details}")

    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    if f"## [{tag_version}] - " not in changelog:
        raise ValueError(f"CHANGELOG.md has no dated [{tag_version}] release entry")
    notes = ROOT / ".github" / "release-notes" / f"v{tag_version}.md"
    if not notes.is_file():
        raise ValueError(f"Missing release notes: {notes.relative_to(ROOT)}")
    verify_install_entries(tag_version)
    return tag_version


def _metadata_version(payload: bytes, source: str) -> str:
    version = BytesParser(policy=default).parsebytes(payload).get("Version")
    if not version:
        raise ValueError(f"Distribution metadata has no Version field: {source}")
    return str(version)


def verify_distributions(dist_dir: Path, version: str) -> None:
    wheel = dist_dir / f"cpdatakit-{version}-py3-none-any.whl"
    sdist = dist_dir / f"cpdatakit-{version}.tar.gz"
    actual = {path.name for path in dist_dir.iterdir() if path.is_file()}
    expected = {wheel.name, sdist.name}
    if actual != expected:
        raise ValueError(
            f"Expected exactly {sorted(expected)} in {dist_dir}, found {sorted(actual)}"
        )

    with zipfile.ZipFile(wheel) as archive:
        metadata_name = f"cpdatakit-{version}.dist-info/METADATA"
        try:
            wheel_version = _metadata_version(archive.read(metadata_name), wheel.name)
        except KeyError as error:
            raise ValueError(f"Wheel is missing {metadata_name}") from error
    with tarfile.open(sdist, mode="r:gz") as archive:
        metadata_name = f"cpdatakit-{version}/PKG-INFO"
        try:
            member = archive.getmember(metadata_name)
            stream = archive.extractfile(member)
            if stream is None:
                raise ValueError(f"Source distribution cannot read {metadata_name}")
            sdist_version = _metadata_version(stream.read(), sdist.name)
        except KeyError as error:
            raise ValueError(f"Source distribution is missing {metadata_name}") from error
    if wheel_version != version or sdist_version != version:
        raise ValueError(
            "Distribution versions disagree: "
            f"tag={version}, wheel={wheel_version}, sdist={sdist_version}"
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("tag", help="Exact Git tag to validate, for example v0.1.1")
    parser.add_argument("--dist-dir", type=Path, help="Also verify built wheel and sdist metadata")
    args = parser.parse_args()
    try:
        version = verify_release(args.tag)
        if args.dist_dir is not None:
            verify_distributions(args.dist_dir, version)
    except ValueError as error:
        raise SystemExit(str(error)) from error
    subject = "Release metadata and distribution versions" if args.dist_dir else "Release metadata"
    print(f"{subject} match v{version}")


if __name__ == "__main__":
    main()
