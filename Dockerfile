ARG NODE_IMAGE=node:22-alpine
ARG PYTHON_IMAGE=python:3.13-alpine3.21
FROM --platform=$BUILDPLATFORM ${NODE_IMAGE} AS frontend
WORKDIR /web
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM ${PYTHON_IMAGE} AS backend-check
ENV PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY server/ ./server/
COPY tests/ ./tests/
RUN python -m unittest discover -s tests

FROM ${PYTHON_IMAGE}
RUN addgroup -S -g 10001 embylens && adduser -S -u 10001 -G embylens embylens && mkdir /data && chown embylens:embylens /data
WORKDIR /app
COPY --from=backend-check /app/server ./server
COPY --from=frontend /web/dist ./server/web
ENV DATA_DIR=/data BIND_ADDRESS=0.0.0.0 PORT=8080 PYTHONDONTWRITEBYTECODE=1
USER embylens
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3).close()"
CMD ["python", "server/app.py"]
