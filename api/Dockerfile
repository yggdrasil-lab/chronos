FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 --shell /bin/sh chronos

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY pyproject.toml alembic.ini ./
COPY alembic ./alembic
COPY app ./app
COPY tests ./tests
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh && chown -R chronos:chronos /app

USER chronos

EXPOSE 8000

ENTRYPOINT ["/entrypoint.sh"]
