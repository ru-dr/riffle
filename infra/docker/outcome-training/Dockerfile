# Outcome model training (setup G), containerised (guideline 8.1). Build from the repo root:
#
#   docker build -f infra/docker/outcome-training/Dockerfile -t riffle-outcome-training .
#
# Run with the data release mounted read-only and a folder for the results:
#
#   docker run --rm -v "<data folder>:/data:ro" -v "<results folder>:/out" \
#     -e RIFFLE_DATA=/data/dataset_merged.parquet riffle-outcome-training
#
# It runs the notebook (features, leakage tests, training, both evaluations), bias_report.py, bias_mitigation.py,
# shap_report.py and model_gates.py, then copies pipeline.ipynb, eval/ and models/ to /out. -e RIFFLE_FAST=1 runs
# the notebook's quick check. The container exits 1 if a step or a gate fails; the results are copied either way.
FROM python:3.12-slim-bookworm

# LightGBM needs the OpenMP runtime.
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 MPLBACKEND=Agg

COPY infra/docker/outcome-training/requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt \
    && python -m ipykernel install --name riffle --display-name "riffle"

# Only what the run needs: the notebook folder, the shared feature module and the team's evaluation script.
WORKDIR /riffle
COPY pipelines/training/outcome_features.py pipelines/training/outcome_features.py
COPY pipelines/training/outcome pipelines/training/outcome
COPY pipelines/evaluation/eval_script.py pipelines/evaluation/eval_script.py
COPY infra/docker/outcome-training/run.sh /usr/local/bin/run-outcome-training
# A Windows checkout may give the script CRLF line endings, which bash cannot run: strip them.
RUN sed -i 's/\r$//' /usr/local/bin/run-outcome-training && chmod +x /usr/local/bin/run-outcome-training

WORKDIR /riffle/pipelines/training/outcome
ENTRYPOINT ["run-outcome-training"]
