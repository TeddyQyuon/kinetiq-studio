#!/usr/bin/env bash
# Run from the Kinetiq checkout. Keep the media stack separate from ComfyUI's CUDA wheels.
set -euo pipefail
media_env=${1:?Supply the media environment directory}
apt-get -qq update
apt-get -qq install -y python3-venv ffmpeg espeak-ng libsndfile1
python -m venv "$media_env"
py="$media_env/bin/python"
"$py" -m pip install -q torch==2.8.0 torchaudio==2.8.0 torchvision==0.23.0 --index-url https://download.pytorch.org/whl/cpu
"$py" -m pip install -q ./workers/agent-core -r workers/media/requirements.txt
"$py" -m spacy download en_core_web_sm
"$py" -m nltk.downloader punkt_tab
