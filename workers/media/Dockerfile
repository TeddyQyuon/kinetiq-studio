# Build from the repository's workers directory.
FROM python:3.12-slim-bookworm

WORKDIR /opt/kinetiq
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg espeak-ng libsndfile1 libgomp1 curl && rm -rf /var/lib/apt/lists/*

COPY agent-core /opt/agent-core
RUN pip install --no-cache-dir /opt/agent-core

ARG INSTALL_MODELS=true
COPY media/requirements.txt /tmp/media-requirements.txt
RUN if [ "$INSTALL_MODELS" = true ]; then \
      pip install --no-cache-dir torch==2.8.0 torchaudio==2.8.0 torchvision==0.23.0 \
        --index-url https://download.pytorch.org/whl/cpu && \
      pip install --no-cache-dir -r /tmp/media-requirements.txt && \
      python -m spacy download en_core_web_sm && \
      curl -fsSL https://raw.githubusercontent.com/nltk/nltk_data/gh-pages/packages/tokenizers/punkt_tab.zip -o /tmp/punkt_tab.zip && \
      echo 'e57f64187974277726a3417ca6f181ec5403676c717672eef6a748a7b20e0106  /tmp/punkt_tab.zip' | sha256sum -c - && \
      python -m zipfile -e /tmp/punkt_tab.zip /usr/local/share/nltk_data/tokenizers && rm /tmp/punkt_tab.zip; \
    fi
COPY media/*.py workers/media/
ENTRYPOINT ["python", "-m", "workers.media.runner"]
