FROM python:3.11-slim
LABEL authors="yaa.sh"

# Install inotifywait
RUN apt-get update && apt-get install -y --no-install-recommends inotify-tools \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . /app
RUN chmod +x /app/entrypoint.sh

RUN pip install --no-cache-dir -e .

EXPOSE 5000
ENTRYPOINT ["/app/entrypoint.sh"]