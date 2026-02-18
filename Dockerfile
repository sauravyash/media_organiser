FROM python:3.11-slim
LABEL authors="yaa.sh"

# Install inotifywait for watching the import directory
RUN apt-get update && apt-get install -y --no-install-recommends inotify-tools \
    && rm -rf /var/lib/apt/lists/*

# Install Poetry and project with runtime deps (Flask for web, Pillow for organiser)
ENV POETRY_VERSION=1.7.1 \
    POETRY_HOME="/opt/poetry" \
    POETRY_VIRTUALENVS_CREATE=false
RUN pip install --no-cache-dir "poetry==${POETRY_VERSION}"

WORKDIR /app
COPY . /app
RUN chmod +x /app/entrypoint.sh \
    && poetry install --no-dev --no-interaction --no-ansi \
    && mkdir -p /data/import /data/library

EXPOSE 6767
ENTRYPOINT ["/app/entrypoint.sh"]