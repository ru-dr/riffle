# Airflow with the extra Python packages Riffle's DAGs need. The base image is
# pinned so local and the GCP VM run the same Airflow; bump it deliberately.
FROM apache/airflow:3.3.2-python3.12

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
