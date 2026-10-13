FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY mailer/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY mailer/send.py /app/send.py
ENTRYPOINT ["python", "/app/send.py"]

COPY feedback/ /app/feedback/
