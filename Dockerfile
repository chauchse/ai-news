FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ /app/app/
COPY config.yaml /app/config.yaml

CMD ["python", "-m", "app.collector"]

COPY project_config.py /app/project_config.py
