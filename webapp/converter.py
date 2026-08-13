"""
Обёртка над CLI cronodump.

Конвертация запускается ТОЛЬКО в отдельном процессе, потому что
crodump.croconvert.csv_output() делает chdir() и пишет в stdout —
в рамках веб-воркера это глобальное состояние и гонки между запросами.
Подпроцесс даёт изоляцию, таймаут и защиту от падений парсера.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Файлы, по которым опознаём каталог базы Cronos
DB_MARKERS = ("crobank.dat", "crostru.dat", "croindex.dat")


class ConvertError(RuntimeError):
    pass


@dataclass
class ConvertResult:
    path: Path          # файл, который отдаём пользователю
    filename: str       # имя файла для скачивания
    media_type: str
    stderr: str         # предупреждения парсера — показываем в логах/ответе


def safe_extract(zip_path: Path, dest: Path) -> None:
    """Распаковка с защитой от zip-slip (../ в именах) и симлинков."""
    dest = dest.resolve()
    try:
        zf_ctx = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile:
        raise ConvertError("Загруженный файл не является корректным ZIP-архивом.")
    with zf_ctx as zf:
        for member in zf.infolist():
            name = member.filename
            if name.endswith("/"):
                continue
            target = (dest / name).resolve()
            if not str(target).startswith(str(dest) + os.sep):
                raise ConvertError(f"Небезопасный путь в архиве: {name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)


def find_db_dir(root: Path) -> Path:
    """Ищем каталог с файлами CroBank/CroStru. Берём самый неглубокий."""
    candidates = []
    for path in root.rglob("*"):
        if path.is_file() and path.name.lower() in DB_MARKERS:
            candidates.append(path.parent)
    if not candidates:
        raise ConvertError(
            "В загруженных данных не найдены файлы базы Cronos "
            "(CroBank.dat / CroStru.dat / CroIndex.dat)."
        )
    # самый короткий путь = корень базы, а не вложенный Voc/
    return sorted(set(candidates), key=lambda p: len(p.parts))[0]


def zip_dir(src: Path, out_file: Path) -> None:
    with zipfile.ZipFile(out_file, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(src.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(src))


def _kod_flags(kod_mode: str) -> list[str]:
    return {
        "default": [],
        "strucrack": ["--strucrack"],
        "dbcrack": ["--dbcrack"],
        "nokod": ["--nokod"],
    }.get(kod_mode, [])


def _run(cmd: list[str], timeout: int, stdout_file=None) -> str:
    env = dict(os.environ, PYTHONPATH=str(REPO_ROOT), PYTHONIOENCODING="utf-8")
    try:
        proc = subprocess.run(
            cmd,
            cwd=REPO_ROOT,
            env=env,
            stdout=stdout_file if stdout_file else subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise ConvertError(f"Превышен таймаут конвертации ({timeout} с).")

    stderr = proc.stderr.decode("utf-8", "replace")
    if proc.returncode != 0:
        raise ConvertError(f"croconvert завершился с кодом {proc.returncode}:\n{stderr[-4000:]}")
    return stderr


def convert(db_dir: Path, workdir: Path, fmt: str, kod_mode: str,
            delimiter: str = ",", timeout: int = 900,
            compact: bool = False) -> ConvertResult:
    """fmt: csv | html | postgres | strudump"""
    py = sys.executable
    compact_flag = ["--compact"] if compact else []

    if fmt == "csv":
        outdir = workdir / "dump"          # не создаём: croconvert делает mkdir сам
        stderr = _run(
            [py, "-m", "webapp._croconvert_cli", "--csv", "-d", delimiter,
             "-o", str(outdir), *compact_flag, *_kod_flags(kod_mode), str(db_dir)],
            timeout,
        )
        archive = workdir / "cronodump-csv.zip"
        zip_dir(outdir, archive)
        return ConvertResult(archive, "cronodump-csv.zip", "application/zip", stderr)

    if fmt in ("html", "postgres"):
        ext = "html" if fmt == "html" else "sql"
        media = "text/html; charset=utf-8" if fmt == "html" else "application/sql"
        out_file = workdir / f"cronodump.{ext}"
        with open(out_file, "wb") as fh:
            stderr = _run(
                [py, "-m", "webapp._croconvert_cli", "-t", fmt,
                 *compact_flag, *_kod_flags(kod_mode), str(db_dir)],
                timeout, stdout_file=fh,
            )
        return ConvertResult(out_file, out_file.name, media, stderr)

    if fmt == "strudump":
        out_file = workdir / "strudump.txt"
        with open(out_file, "wb") as fh:
            stderr = _run(
                [py, "-m", "crodump.crodump", *_kod_flags(kod_mode),
                 "strudump", "-v", "-a", str(db_dir)],
                timeout, stdout_file=fh,
            )
        return ConvertResult(out_file, "strudump.txt", "text/plain; charset=utf-8", stderr)

    raise ConvertError(f"Неизвестный формат: {fmt}")
