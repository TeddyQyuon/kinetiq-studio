# Phase 2 Enterprise GPU Worker (RunPod / Modal / Dedicated NVIDIA)
FROM nvidia/cuda:12.4.1-runtime-ubuntu22.04

WORKDIR /opt/kinetiq-worker

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip git ffmpeg \
    && rm -rf /var/lib/apt/lists/*

RUN git clone --depth 1 https://github.com/comfyanonymous/ComfyUI.git /opt/ComfyUI \
    && pip3 install --no-cache-dir -r /opt/ComfyUI/requirements.txt

COPY agent-core /opt/agent-core
RUN pip3 install --no-cache-dir -e /opt/agent-core

COPY gpu/requirements.txt ./
RUN pip3 install --no-cache-dir -r requirements.txt
COPY gpu/runner.py ./runner.py

ENV COMFY_URL=http://127.0.0.1:8188
CMD ["python3", "runner.py", "--profile", "ltx25_pro", "--provider", "runpod"]
