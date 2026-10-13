FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY mailer/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY mailer/ /app/mailer/
ENTRYPOINT ["python", "-m", "mailer.send"]

COPY project_config.py /app/project_config.py
