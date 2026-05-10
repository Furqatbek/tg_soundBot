FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app

RUN mkdir -p /data
ENV DATABASE_URL=sqlite+aiosqlite:////data/sounds.db \
    PORT=8000

EXPOSE 8000

CMD ["python", "-m", "app.main"]
