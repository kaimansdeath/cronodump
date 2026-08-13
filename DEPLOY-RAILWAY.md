# Развёртывание cronodump на Railway

`cronodump` — консольная утилита, а Railway обслуживает долгоживущие процессы.
Поэтому добавлена веб-обёртка `webapp/` (FastAPI): загрузка ZIP с базой Cronos →
конвертация → скачивание результата.

## Что добавлено

| Файл | Назначение |
|---|---|
| `webapp/main.py` | HTTP-сервис: форма загрузки, HTTP Basic, отдача результата |
| `webapp/converter.py` | Запуск конвертации в подпроцессе, безопасная распаковка ZIP |
| `webapp/_croconvert_cli.py` | Обёртка CLI: диагностика в stderr, чтобы не портить .html/.sql |
| `Dockerfile` | Сборка образа, запуск на `$PORT` |
| `railway.json` | Билдер + healthcheck `/healthz` |
| `requirements.txt` | fastapi, uvicorn, python-multipart, Jinja2 |
| `Procfile`, `.python-version` | Резерв, если собирать через Nixpacks вместо Docker |
| `.dockerignore` | Исключает `test_data/`, `docs/`, `.git` из образа |

## Исправления в исходном коде

1. **`crodump/croconvert.py`** — опции `--strucrack` и `--dbcrack` падали с
   `AttributeError: 'Cls' object has no attribute 'compact'`. Добавлено
   `cargs.compact = args.compact` в обеих ветках. Это баг апстрима, а не
   следствие обёртки; воспроизводится и в чистом CLI.

2. **Диагностика в stdout.** Библиотека печатает `Warning: ...` через `print()`
   в stdout. Для шаблонов `html`/`postgres` stdout — это сам результат, поэтому
   предупреждения попадали внутрь файла: строка `Warning: ...` перед
   `CREATE TABLE` делает SQL-дамп неимпортируемым. В `_croconvert_cli.py`
   `builtins.print` перенаправлен в stderr; предупреждения возвращаются
   клиенту в заголовке `X-Cronodump-Warnings`.

## Деплой

```bash
git init && git add -A && git commit -m "railway deploy"
# запушить в GitHub, затем: Railway → New Project → Deploy from GitHub repo
```

Либо через CLI:

```bash
npm i -g @railway/cli
railway login
railway init
railway up
```

Railway подхватит `railway.json` и соберёт по `Dockerfile`.

## Переменные окружения

В Railway → Variables:

| Переменная | По умолчанию | Описание |
|---|---|---|
| `APP_PASSWORD` | — | **Обязательна.** Без неё сервис отвечает 503 |
| `APP_USER` | `admin` | Логин HTTP Basic |
| `MAX_UPLOAD_MB` | `200` | Лимит размера загружаемого ZIP |
| `CONVERT_TIMEOUT` | `900` | Таймаут конвертации, секунды |
| `WORK_DIR` | `/tmp/cronodump` | Каталог временных файлов |
| `PORT` | — | Подставляется Railway, трогать не нужно |

`PORT` раскрывается в shell-форме `CMD`, поэтому переопределять `startCommand`
не требуется. Домен назначается в Settings → Networking → Generate Domain.

## Эксплуатационные ограничения

- **Диск эфемерный.** Всё в `/tmp` исчезает при передеплое и рестарте. Это
  осознанно: временные каталоги удаляются сразу после отдачи ответа. Если нужно
  хранить дампы — подключите Volume и смонтируйте его, например в `/data`,
  затем задайте `WORK_DIR=/data/work`.
- **Память.** Шаблон `html` вшивает все файлы и картинки как base64 в один
  документ — на крупной базе это гигабайты в RAM и мгновенный OOM на младших
  планах. Для больших баз выбирайте CSV и включайте галочку `--compact`.
- **Долгие запросы.** Конвертация многогигабайтной базы может не уложиться в
  таймаут edge-прокси Railway. Для таких объёмов веб-форма — неподходящий
  инструмент: запускайте разово через `railway run bin/croconvert --csv <path>`
  или отдельным worker-сервисом с Volume.
- **Доступ.** Cronos-базы почти всегда содержат персональные данные, поэтому
  Basic-аутентификация включена принудительно. Публичный URL Railway
  индексируется — не оставляйте сервис без пароля и рассмотрите
  ограничение по IP на уровне Cloudflare, если ставите свой домен.

## Локальная проверка

```bash
pip install -r requirements.txt
export APP_PASSWORD=secret
PYTHONPATH=. uvicorn webapp.main:app --port 8000
# http://127.0.0.1:8000 → логин admin / secret
```

Проверка Docker-сборки:

```bash
docker build -t cronodump .
docker run -p 8000:8000 -e APP_PASSWORD=secret -e PORT=8000 cronodump
```

## API без формы

```bash
curl -u admin:secret \
     -F "archive=@base.zip" \
     -F "fmt=csv" \
     -F "kod_mode=strucrack" \
     -o dump.zip \
     https://<ваш-домен>.up.railway.app/convert
```

`fmt`: `csv` | `html` | `postgres` | `strudump`
`kod_mode`: `default` | `strucrack` | `dbcrack` | `nokod`
