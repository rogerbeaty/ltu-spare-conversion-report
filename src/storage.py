from __future__ import annotations

import hashlib
import re
from pathlib import Path


def save_uploaded_csv(upload_dir: Path, original_name: str, data: bytes) -> Path:
    upload_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(data).hexdigest()
    safe_name = _safe_csv_name(original_name)
    prior_versions = [
        path
        for path in upload_dir.iterdir()
        if path.is_file()
        and path.suffix.lower() == ".csv"
        and (path.name.partition("__")[2] or path.name).casefold() == safe_name.casefold()
    ]

    existing = next(upload_dir.glob(f"{digest}__*.csv"), None)
    if existing is not None and existing.is_file():
        for prior in prior_versions:
            if prior != existing:
                prior.unlink()
        return existing

    target = upload_dir / f"{digest}__{safe_name}"
    temporary = upload_dir / f".{digest}.tmp"
    temporary.write_bytes(data)
    temporary.replace(target)
    for prior in prior_versions:
        if prior != target:
            prior.unlink()
    return target


def list_uploaded_csvs(upload_dir: Path) -> list[Path]:
    if not upload_dir.exists():
        return []
    files = [path for path in upload_dir.iterdir() if path.is_file() and path.suffix.lower() == ".csv"]
    return sorted(files, key=lambda path: (path.stat().st_mtime, path.name), reverse=True)


def delete_uploaded_csv(upload_dir: Path, stored_name: str) -> bool:
    root = upload_dir.resolve()
    target = (root / stored_name).resolve()
    if target.parent != root or target.suffix.lower() != ".csv":
        raise ValueError("Invalid stored CSV name")
    if not target.exists():
        return False
    target.unlink()
    return True


def uploaded_csv_label(path: Path) -> str:
    digest, separator, original_name = path.name.partition("__")
    if not separator:
        return path.name
    return f"{original_name} ({digest[:8]})"


def _safe_csv_name(original_name: str) -> str:
    name = Path(original_name).name.strip()
    stem = Path(name).stem or "upload"
    safe_stem = re.sub(r"[^A-Za-z0-9._ -]+", "_", stem).strip(" ._") or "upload"
    return f"{safe_stem}.csv"
