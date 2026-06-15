FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libheif-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY smugly/ smugly/

ENV SMUGLY_PHOTO_DIR=/photos \
    SMUGLY_CONFIG_DIR=/config \
    SMUGLY_PORT=8080

EXPOSE 8080

CMD ["sh", "-c", "uvicorn smugly.main:app --host 0.0.0.0 --port ${SMUGLY_PORT}"]
