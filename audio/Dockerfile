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

COPY audio/requirements.txt /tmp/audio-requirements.txt
RUN pip install --no-cache-dir -r /tmp/audio-requirements.txt && \
    python -c "import spacy; assert spacy.util.is_package('en_core_web_sm'); spacy.load('en_core_web_sm', enable=['tok2vec', 'tagger'])" && \
    pip check

WORKDIR /app

COPY audio/tts.py /app/tts.py

ENTRYPOINT ["python", "/app/tts.py"]

COPY project_config.py /app/project_config.py
