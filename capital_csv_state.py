"""Pack raw CSV checkpoints into Git-sized files and restore verified bytes."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import tempfile

THRESHOLD = 95 * 1024 * 1024
CHUNK_SIZE = 40 * 1024 * 1024


def safe(root: Path, relative: str) -> Path:
    part = PurePosixPath(relative)
    if (not relative or part.is_absolute() or part.as_posix() != relative
            or ".." in part.parts or "\\" in relative or "\x00" in relative):
        raise ValueError("Unsafe checkpoint path")
    path = root.absolute() / relative
    if any(item.is_symlink() for item in (path, *path.parents)):
        raise ValueError("Checkpoint symlinks are forbidden")
    return path


def allowed(relative: str) -> bool:
    parts = PurePosixPath(relative).parts
    return ((len(parts) == 1 and relative.endswith(".json")) or
            (parts[0] in {"seoul", "gyeonggi", "incheon", "quarantine"}
             and relative.endswith(".csv.gz")))


def files(root: Path):
    safe(root, "manifest.json")
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        safe(root, relative)
        if path.is_file() and allowed(relative):
            yield relative, path


def digest(path: Path) -> tuple[int, str]:
    size, sha = 0, hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(block)
            sha.update(block)
    return size, sha.hexdigest()


def atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def copy_verified(inputs: list[Path], target: Path, size: int, sha256: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            for path in inputs:
                with path.open("rb") as source:
                    for block in iter(lambda: source.read(1024 * 1024), b""):
                        output.write(block)
            output.flush()
            os.fsync(output.fileno())
        if digest(Path(temporary)) != (size, sha256):
            raise ValueError("Checkpoint byte length or SHA256 mismatch")
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def separate(left: Path, right: Path) -> None:
    left, right = left.absolute(), right.absolute()
    if left == right or left in right.parents or right in left.parents:
        raise ValueError("Source, state and output must be separate directories")


def pack(source: Path, state: Path, threshold: int = THRESHOLD,
         chunk_size: int = CHUNK_SIZE) -> None:
    separate(source, state)
    if not source.is_dir():
        raise ValueError("Checkpoint source directory does not exist")
    if not 0 < chunk_size < threshold <= THRESHOLD:
        raise ValueError("Require 0 < chunk size < threshold <= 95 MiB")
    index = {"version": 1, "files": {}}
    for relative, path in files(source):
        size, sha = digest(path)
        entry = {"size": size, "sha256": sha}
        target = safe(state, "collection/" + relative)
        if size < threshold:
            copy_verified([path], target, size, sha)
            entry["file"] = "collection/" + relative
        else:
            entry["chunks"] = []
            with path.open("rb") as stream:
                for number, block in enumerate(iter(lambda: stream.read(chunk_size), b"")):
                    name = f"objects/{sha}-{number:04d}.part"
                    atomic(safe(state, name), block)
                    entry["chunks"].append(name)
            if digest(path) != (size, sha):
                raise ValueError("Source changed during checkpoint packing")
            target.unlink(missing_ok=True)
        index["files"][relative] = entry
    atomic(safe(state, "state_files.json"), (json.dumps(index, indent=2) + "\n").encode())


def restore(state: Path, output: Path) -> None:
    separate(state, output)
    index = json.loads(safe(state, "state_files.json").read_text())
    if index.get("version") != 1:
        raise ValueError("Unsupported checkpoint index")
    for relative, entry in index["files"].items():
        target = safe(output, relative)
        if not allowed(relative):
            raise ValueError("Checkpoint file is outside the raw-data allowlist")
        names = entry.get("chunks", [entry.get("file")])
        if "chunks" in entry:
            expected = [f"objects/{entry['sha256']}-{n:04d}.part" for n in range(len(names))]
            if not names or names != expected or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]):
                raise ValueError("Invalid checkpoint chunk paths")
        elif names != ["collection/" + relative]:
            raise ValueError("Invalid checkpoint file path")
        copy_verified([safe(state, name) for name in names], target, entry["size"], entry["sha256"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for command, argument in (("pack", "--source"), ("restore", "--output")):
        child = sub.add_parser(command)
        child.add_argument("--state", type=Path, required=True)
        child.add_argument(argument, type=Path, required=True)
    args = parser.parse_args()
    pack(args.source, args.state) if args.command == "pack" else restore(args.state, args.output)


if __name__ == "__main__":
    main()
