FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY crodump/ ./crodump/
COPY templates/ ./templates/
COPY webapp/ ./webapp/

# Непривилегированный пользователь
RUN useradd -m -u 10001 app && mkdir -p /tmp/cronodump && chown -R app /tmp/cronodump
USER app

# Railway подставляет $PORT — форма shell обязательна для его раскрытия
CMD uvicorn webapp.main:app --host 0.0.0.0 --port ${PORT:-8000} --timeout-keep-alive 120
