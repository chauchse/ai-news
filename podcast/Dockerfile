FROM python:3.11-slim
RUN pip install --no-cache-dir paramiko==4.0.0 PyYAML==6.0.2
WORKDIR /app
COPY podcast/publish.py /app/publish.py
ENTRYPOINT ["python", "/app/publish.py"]

COPY project_config.py /app/project_config.py
