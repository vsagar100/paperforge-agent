FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN groupadd --system paperforge && useradd --system --gid paperforge --create-home paperforge
WORKDIR /app

COPY pyproject.toml README.md ./
COPY config ./config
COPY src ./src
RUN pip install ".[documents]"

USER paperforge
ENTRYPOINT ["paperforge"]
CMD ["--help"]
