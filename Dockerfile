# syntax=docker/dockerfile:1
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install .

COPY config ./config
COPY migrations ./migrations
RUN useradd --create-home --uid 10001 bot && mkdir -p /app/data && chown bot:bot /app/data
USER bot

CMD ["python", "-m", "bot.main", "run"]

# Imagen para CI/tests: mismas dependencias + herramientas de desarrollo.
FROM runtime AS dev
USER root
RUN pip install ".[dev]"
COPY tests ./tests
COPY scripts ./scripts
COPY .gitignore .env.example ./
RUN chown -R bot:bot /app
USER bot
CMD ["sh", "scripts/ci.sh"]
