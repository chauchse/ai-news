FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    HF_HUB_OFFLINE=1 \
    HF_HOME=/tmp/huggingface

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    ffmpeg espeak-ng libsndfile1 && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    'torch==2.5.1' \
    'torchaudio==2.5.1' \
    --index-url https://download.pytorch.org/whl/cpu

RUN pip install --no-cache-dir \
    'piper-tts==1.3.0' \
    'PyYAML>=6,<7' \
    'kokoro==0.9.4' \
    'misaki[en,fr]>=0.9.4,<1' \
    'soundfile>=0.12' \
    'transformers==4.46.3'

WORKDIR /app

COPY audio/tts.py /app/tts.py

ENTRYPOINT ["python", "/app/tts.py"]