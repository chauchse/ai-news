FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OMP_NUM_THREADS=4

WORKDIR /app

COPY embeddings/requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY embeddings/ /app/embeddings/

CMD ["python", "-m", "embeddings.worker"]

COPY project_config.py /app/project_config.py
