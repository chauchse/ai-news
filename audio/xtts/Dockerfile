FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    COQUI_TOS_AGREED=1 \
    TTS_HOME=/models

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    ffmpeg espeak-ng libsndfile1 && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    'torch==2.5.1' 'torchaudio==2.5.1' \
    --index-url https://download.pytorch.org/whl/cpu

RUN pip install --no-cache-dir \
    'coqui-tts==0.27.2' \
    'transformers==4.55.4'

WORKDIR /app

COPY audio/tts.py /app/tts.py
ENTRYPOINT ["python", "/app/tts.py"]
