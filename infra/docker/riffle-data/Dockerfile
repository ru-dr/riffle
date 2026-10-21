# The team store: ingest, pages and the BigQuery loader (ADR 0014). Built
# locally and never pulled, like the Airflow image (ADR 0013).
FROM python:3.12-slim

RUN useradd --create-home --uid 10001 riffle && mkdir /data && chown riffle /data
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
WORKDIR /app
COPY server/ /app/

USER riffle
ENV RIFFLE_DATA=/data PORT=8470 PYTHONUNBUFFERED=1
EXPOSE 8470
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request as u;u.urlopen('http://127.0.0.1:8470/healthz',timeout=3)"
CMD ["python", "app.py"]
