"""Check the explicit public file inventory before committing or packaging."""

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
CLIENT = "addons/narrate_to_sim/"
PRIVATE_REPO = "https://github.com/" + "ioe-wang-ihub/" + "carla_based_experiment_platform"
PROJECT_FILES = {
    ".gitattributes", ".gitignore", ".github/workflows/tests.yml",
    "README.md", "License.txt", "Notices.txt", "CITATION.cff",
    "CONTRIBUTING.md", "CODE_OF_CONDUCT.md", "CHANGELOG.md",
    "docs/VERIFICATION.md", "docs/CARLA_SETUP.md", "tools/check_public_source.py",
}


def main():
    manifest = json.loads((ROOT / CLIENT / "source_manifest.json").read_text(encoding="utf-8"))
    entries = manifest["files"]
    allowed = PROJECT_FILES | {CLIENT + name for name in entries} | {CLIENT + "source_manifest.json"}
    tracked = set(filter(None, subprocess.check_output(
        ["git", "ls-files", "-z"], cwd=ROOT
    ).decode("utf-8").split("\0")))
    if tracked != allowed:
        raise SystemExit(f"Public inventory mismatch: extra={sorted(tracked - allowed)}; missing={sorted(allowed - tracked)}")
    for name in sorted(tracked):
        path = (ROOT / name).resolve()
        if not path.is_relative_to(ROOT) or not path.is_file() or path.stat().st_size > 5_000_000:
            raise SystemExit(f"Invalid public source file: {name}")
        if path.suffix.lower() not in {".py", ".md", ".txt", ".json", ".ps1", ".bat", ".sce", ".cff", ".yml"} and name not in {".gitignore", ".gitattributes"}:
            raise SystemExit(f"Unexpected public file type: {name}")
        text = path.read_text(encoding="utf-8")
        if PRIVATE_REPO in text:
            raise SystemExit(f"Private project link remains: {name}")
    for name, digest in entries.items():
        if hashlib.sha256((ROOT / CLIENT / name).read_bytes()).hexdigest() != digest:
            raise SystemExit(f"Client manifest mismatch: {name}")
    print(f"PASS: {len(tracked)} allowlisted public files; {len(entries)} client hashes; no private-project links")


if __name__ == "__main__":
    main()
