"""Refresh the source-only manifest after reviewing intentional source changes."""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIRS = (
    "carla_nl", "tools", "tests", "examples", "third_party_licenses",
    "scanner_prompt_gui_to_carla_0913_source_20260524",
)
SOURCE_SUFFIXES = {".py", ".ps1", ".bat", ".json", ".md", ".txt", ".sce"}


def source_files(root):
    root = Path(root).resolve()
    candidates = list(root.iterdir())
    for directory in SOURCE_DIRS:
        candidates.extend((root / directory).rglob("*"))
    return sorted(
        path.relative_to(root).as_posix() for path in candidates
        if path.is_file() and path.suffix.lower() in SOURCE_SUFFIXES
        and path.name != "source_manifest.json"
        and not any(part.startswith(".") or part == "__pycache__"
                    for part in path.relative_to(root).parts)
    )


def main():
    manifest_path = ROOT / "source_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["purpose"] = "Source-only stock-CARLA client; no bundled simulator or runtime"
    manifest["source_license"] = "GPL-3.0-or-later"
    manifest["files"] = {}
    for name in source_files(ROOT):
        path = ROOT / name
        # Match .gitattributes even when contributors use CRLF-configured Git.
        data = path.read_bytes().replace(b"\r\n", b"\n")
        if data != path.read_bytes():
            path.write_bytes(data)
        manifest["files"][name] = hashlib.sha256(data).hexdigest()
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n",
                             encoding="utf-8", newline="\n")
    print(f"Recorded {len(manifest['files'])} source/document files (LF, SHA-256).")


if __name__ == "__main__":
    main()
