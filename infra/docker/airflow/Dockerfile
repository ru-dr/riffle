# Airflow with the extra Python packages Riffle's DAGs need. The base image is
# pinned so local and the GCP VM run the same Airflow; bump it deliberately.
ARG AIRFLOW_VERSION=3.3.2
FROM apache/airflow:${AIRFLOW_VERSION}-python3.12

# Scratch space for the riffle_miner DAG (gathered shard files, the DVC workspace). It is made here, owned by the
# airflow user, so the named volume mounted over it starts out writable; a volume made root-owned cannot be written.
USER root
RUN mkdir -p /opt/riffle/work && chown -R airflow:0 /opt/riffle/work
USER airflow

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# The data pipeline's stages run in their own virtualenv, so pandas and Great Expectations
# cannot conflict with Airflow's dependencies (DAGs call /home/airflow/riffle-venv/bin/python).
COPY requirements-pipeline.txt /tmp/requirements-pipeline.txt
RUN python -m venv /home/airflow/riffle-venv \
    && /home/airflow/riffle-venv/bin/pip install --no-cache-dir -r /tmp/requirements-pipeline.txt

# Start-up helpers the Compose file calls: login-file validation and the
# database URL. They are files, not inline YAML, so they can be linted and tested.
COPY scripts/ /opt/airflow/scripts/
