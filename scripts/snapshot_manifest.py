#!/usr/bin/env python3
"""快照 manifest：逐文件 size + SHA-256 清单（P 核验补充项）。

用法：
  python3 scripts/snapshot_manifest.py gen <root_dir> <out_json> [extra_paths...]
  python3 scripts/snapshot_manifest.py verify <root_dir> <manifest_json>
"""
import hashlib
import json
import sys
from pathlib import Path

SCHEMA_VERSION = 1


def _scan(root: Path, extras):
    paths = []
    for p in sorted(root.rglob("*")):
        if p.is_file():
            paths.append(p.relative_to(root).as_posix())
    for extra in extras:
        ep = Path(extra)
        if ep.is_file():
            paths.append(ep.as_posix())
    return sorted(set(paths))


def _digest(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def gen(root: Path, out: Path, extras):
    root = root.resolve()
    files = []
    for rel in _scan(root, extras):
        p = root.parent / rel if not (root / rel).exists() else (root / rel)
        candidates = [root / rel, root.parent / rel]
        existing = next((c for c in candidates if c.is_file()), None)
        if existing is None:
            continue
        files.append({"path": rel, "size": existing.stat().st_size, "sha256": _digest(existing)})
    manifest = {"schema_version": SCHEMA_VERSION, "files": files}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"manifest generated: {len(files)} files -> {out}")
    return 0


def verify(root: Path, manifest_path: Path) -> int:
    root = root.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    files = manifest.get("files") or []
    bad = 0
    for item in files:
        p = root / item["path"]
        if not p.exists():
            print(f"MISSING {item['path']}")
            bad += 1
            continue
        if p.stat().st_size != item["size"]:
            print(f"SIZE_MISMATCH {item['path']} expect={item['size']} got={p.stat().st_size}")
            bad += 1
            continue
        if _digest(p) != item["sha256"]:
            print(f"SHA_MISMATCH {item['path']}")
            bad += 1
    print(f"manifest verify: {len(files) - bad}/{len(files)} ok" if files else "manifest verify: empty")
    return 1 if bad else 0


def main(argv):
    if len(argv) < 3:
        print(__doc__, file=sys.stderr)
        return 2
    cmd, root, target = argv[0], Path(argv[1]), Path(argv[2])
    extras = argv[3:]
    if cmd == "gen":
        return gen(root, target, extras)
    if cmd == "verify":
        return verify(root, target)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
