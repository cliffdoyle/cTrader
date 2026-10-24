FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    ICTSCAN_DATA=/data ICTSCAN_HOST=0.0.0.0 ICTSCAN_PORT=8765 ICTSCAN_SOURCE=openapi

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY ictscan ./ictscan
RUN useradd --system --uid 10001 scanner && mkdir -p /data && chown scanner /data
USER scanner

EXPOSE 8765
HEALTHCHECK --interval=60s --timeout=10s --start-period=60s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/healthz', timeout=5)"
CMD ["python", "-m", "ictscan"]
