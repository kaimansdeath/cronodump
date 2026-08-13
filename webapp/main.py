"""
Веб-обёртка над cronodump для развёртывания на Railway.

Переменные окружения:
  APP_PASSWORD    — пароль HTTP Basic (ОБЯЗАТЕЛЬНО, иначе /convert отключён)
  APP_USER        — логин, по умолчанию "admin"
  MAX_UPLOAD_MB   — лимит размера загрузки, по умолчанию 200
  CONVERT_TIMEOUT — таймаут конвертации в секундах, по умолчанию 900
  WORK_DIR        — каталог для временных файлов, по умолчанию /tmp/cronodump
  PORT            — подставляется Railway автоматически
"""
from __future__ import annotations

import os
import secrets
import shutil
import tempfile
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from starlette.background import BackgroundTask

from .converter import ConvertError, convert, find_db_dir, safe_extract

APP_USER = os.getenv("APP_USER", "admin")
APP_PASSWORD = os.getenv("APP_PASSWORD", "")
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "200"))
CONVERT_TIMEOUT = int(os.getenv("CONVERT_TIMEOUT", "900"))
WORK_DIR = Path(os.getenv("WORK_DIR", "/tmp/cronodump"))

app = FastAPI(title="cronodump web", docs_url=None, redoc_url=None)
security = HTTPBasic(auto_error=True)


def auth(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    if not APP_PASSWORD:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Не задана переменная окружения APP_PASSWORD — сервис закрыт.",
        )
    ok_user = secrets.compare_digest(credentials.username, APP_USER)
    ok_pass = secrets.compare_digest(credentials.password, APP_PASSWORD)
    if not (ok_user and ok_pass):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Неверные учётные данные",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


@app.get("/healthz", response_class=PlainTextResponse)
def healthz() -> str:
    """Healthcheck для Railway — без авторизации."""
    return "ok"


PAGE = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>cronodump</title>
<style>
 body{font:15px/1.5 system-ui,sans-serif;max-width:640px;margin:40px auto;padding:0 16px;color:#1a1a1a}
 h1{font-size:22px;margin-bottom:4px} p.sub{color:#666;margin-top:0}
 label{display:block;margin:16px 0 4px;font-weight:600}
 select,input[type=file],input[type=text]{width:100%;padding:8px;border:1px solid #ccc;border-radius:6px;box-sizing:border-box}
 button{margin-top:24px;padding:10px 20px;border:0;border-radius:6px;background:#1a1a1a;color:#fff;font-size:15px;cursor:pointer}
 .warn{background:#fff4e5;border:1px solid #f0c17a;padding:12px;border-radius:6px}
 small{color:#666}
</style></head><body>
<h1>cronodump</h1>
<p class="sub">Конвертация баз CronosPro в CSV / HTML / PostgreSQL</p>
<form action="/convert" method="post" enctype="multipart/form-data">
  <label>ZIP-архив с файлами базы (CroBank.dat, CroBank.tad, CroStru.*, CroIndex.*)</label>
  <input type="file" name="archive" accept=".zip" required>
  <label>Формат вывода</label>
  <select name="fmt">
    <option value="csv">CSV + файлы (ZIP)</option>
    <option value="html">HTML (одним файлом)</option>
    <option value="postgres">PostgreSQL (.sql)</option>
    <option value="strudump">strudump — метаданные, диагностика</option>
  </select>
  <label>KOD-таблица</label>
  <select name="kod_mode">
    <option value="default">Стандартная</option>
    <option value="strucrack">--strucrack (вывести из CroStru.dat)</option>
    <option value="dbcrack">--dbcrack (вывести из CroIndex+CroBank)</option>
    <option value="nokod">--nokod (без декодирования)</option>
  </select>
  <label>Разделитель CSV</label>
  <input type="text" name="delimiter" value="," maxlength="1">
  <label><input type="checkbox" name="compact" value="1" style="width:auto"> Экономить память (--compact, для больших баз)</label>
  <button type="submit">Конвертировать</button>
</form>
<p><small>Лимит загрузки: __MAX__ МБ. Таймаут: __TIMEOUT__ с.
Файлы удаляются сразу после отдачи результата.</small></p>
</body></html>"""


@app.get("/", response_class=HTMLResponse)
def index(user: str = Depends(auth)) -> str:
    return (PAGE.replace("__MAX__", str(MAX_UPLOAD_MB))
                .replace("__TIMEOUT__", str(CONVERT_TIMEOUT)))


@app.post("/convert")
async def do_convert(
    archive: UploadFile = File(...),
    fmt: str = Form("csv"),
    kod_mode: str = Form("default"),
    delimiter: str = Form(","),
    compact: str = Form(""),
    user: str = Depends(auth),
):
    if fmt not in ("csv", "html", "postgres", "strudump"):
        raise HTTPException(400, "Недопустимый формат")
    if len(delimiter) != 1:
        delimiter = ","

    WORK_DIR.mkdir(parents=True, exist_ok=True)
    workdir = Path(tempfile.mkdtemp(dir=WORK_DIR))
    cleanup = BackgroundTask(shutil.rmtree, workdir, ignore_errors=True)

    try:
        # Потоковая запись загрузки с контролем размера
        upload_path = workdir / "upload.zip"
        limit = MAX_UPLOAD_MB * 1024 * 1024
        written = 0
        with open(upload_path, "wb") as fh:
            while chunk := await archive.read(1024 * 1024):
                written += len(chunk)
                if written > limit:
                    raise HTTPException(413, f"Файл больше {MAX_UPLOAD_MB} МБ")
                fh.write(chunk)

        extracted = workdir / "src"
        extracted.mkdir()
        safe_extract(upload_path, extracted)
        upload_path.unlink(missing_ok=True)

        db_dir = find_db_dir(extracted)
        result = convert(db_dir, workdir, fmt, kod_mode, delimiter,
                         CONVERT_TIMEOUT, compact=bool(compact))

    except HTTPException:
        shutil.rmtree(workdir, ignore_errors=True)
        raise
    except ConvertError as exc:
        shutil.rmtree(workdir, ignore_errors=True)
        raise HTTPException(422, str(exc))
    except Exception as exc:  # noqa: BLE001
        shutil.rmtree(workdir, ignore_errors=True)
        raise HTTPException(500, f"Внутренняя ошибка: {exc}")

    headers = {"Content-Disposition": f'attachment; filename="{result.filename}"'}
    if result.stderr.strip():
        # предупреждения парсера — в заголовок, чтобы не ломать бинарный ответ
        headers["X-Cronodump-Warnings"] = result.stderr.strip().replace("\n", " | ")[:900]

    return FileResponse(
        result.path,
        media_type=result.media_type,
        headers=headers,
        background=cleanup,
    )
