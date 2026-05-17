FROM python:3.12-alpine

RUN apk add --no-cache tini curl tzdata su-exec && \
    SUPERCRONIC_URL=https://github.com/aptible/supercronic/releases/download/v0.2.33/supercronic-linux-amd64 && \
    curl -fsSL "$SUPERCRONIC_URL" -o /usr/local/bin/supercronic && \
    chmod +x /usr/local/bin/supercronic

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ .
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

ENTRYPOINT ["/sbin/tini", "--", "/app/entrypoint.sh"]
