# scorer: ranks each live PR with the outcome model (setup G). Build from the repo root, like the training image,
# because it needs the shared feature module and the contracts:
#
#   docker build -f services/scorer/Dockerfile -t riffle-scorer .
#
# The model is not in the image: it is downloaded from MLflow at start and checked every minute (README.md).
# Same base and same model libraries as infra/docker/outcome-training, so a saved model computes the same here.
FROM python:3.12-slim-bookworm

# LightGBM needs the OpenMP runtime.
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*

# HOME=/tmp: the root filesystem is read-only in the cluster and /tmp is the one writable place (MLflow's downloads).
# Short MLflow retries: the model check runs every minute anyway, and a slow failure would delay the first one.
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 HOME=/tmp \
    PYTHONPATH=/app CONTRACTS_DIR=/app/contracts \
    MLFLOW_HTTP_REQUEST_MAX_RETRIES=2 MLFLOW_HTTP_REQUEST_TIMEOUT=60

COPY services/scorer/requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt && rm /tmp/requirements.txt

WORKDIR /app
# The contracts it checks messages against, the feature module shared with training, and the service.
COPY contracts/pr_features.schema.json contracts/mined_dataset.schema.json \
     contracts/score_result.schema.json contracts/score_log.schema.json contracts/
COPY pipelines/training/outcome_features.py outcome_features.py
COPY services/scorer/scorer scorer

# Numeric, so Kubernetes' runAsNonRoot can check it.
USER 10001:10001
EXPOSE 8080 9090
ENTRYPOINT ["python", "-m", "scorer"]
