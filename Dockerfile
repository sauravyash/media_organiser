FROM python:3.11-slim
LABEL authors="yaa.sh"

# Install inotifywait
RUN apt-get update && apt-get install -y --no-install-recommends inotify-tools \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY media_organiser/* /app/*
RUN chmod +x /app/entrypoint.sh

# No Python deps required
ENTRYPOINT ["/app/entrypoint.sh"]