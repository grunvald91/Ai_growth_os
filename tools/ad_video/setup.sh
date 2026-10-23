#!/usr/bin/env bash
# Разовая подготовка среды (Codespace или отдельный сервер под рендер): браузер, ffmpeg, шрифт, голос.
set -euo pipefail
sudo apt-get update -qq
sudo apt-get install -y -qq ffmpeg fonts-inter fonts-dejavu-core
python3 -m pip install -q --user numpy pillow playwright piper-tts faster-whisper
python3 -m playwright install --with-deps chromium
V="${AD_VOICE_DIR:-$HOME/.cache/ad_video/voices}"; mkdir -p "$V"
BASE=https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/irina/medium
for f in ru_RU-irina-medium.onnx ru_RU-irina-medium.onnx.json; do [ -s "$V/$f" ] || curl -fsSL -o "$V/$f" "$BASE/$f"; done
echo "готово"
