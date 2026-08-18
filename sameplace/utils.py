from pathlib import Path
import re
import shutil
import unicodedata


def normalize_filename(name: str) -> str:
    name = unicodedata.normalize('NFKD', name)
    name = name.encode('ascii', 'ignore').decode('ascii')
    name = re.sub(r"[^\w\s-]", "", name.lower())
    name = re.sub(r"[-\s]+", "-", name).strip("-_")
    return name


def force_empty_dir(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)
