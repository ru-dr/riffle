# scorelog-writer: score-log messages to encrypted Parquet per tenant and day (ADR 0024, ADR 0028).
# Build from the repo root, for the contract and the shared flat-form module:
#
#   docker build -f services/scorelog-writer/Dockerfile -t riffle-scorelog-writer .
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 HOME=/tmp \
    PYTHONPATH=/app CONTRACTS_DIR=/app/contracts

COPY services/scorelog-writer/requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt && rm /tmp/requirements.txt

WORKDIR /app
# The contract it checks rows against, the flat form shared by every contract (standard library only),
# and the service.
COPY contracts/score_log.schema.json contracts/
COPY contracts/flatten.py flatten.py
COPY services/scorelog-writer/scorelog_writer scorelog_writer

# Numeric, so Kubernetes' runAsNonRoot can check it.
USER 10001:10001
EXPOSE 8080 9090
ENTRYPOINT ["python", "-m", "scorelog_writer"]
