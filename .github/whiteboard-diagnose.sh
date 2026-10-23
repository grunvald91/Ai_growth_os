#!/usr/bin/env bash
# One tiny ClaudeHub vision probe per candidate model (64x64 image). Prints status and answer length only.
set -u
API=$(docker ps --filter label=com.docker.compose.project=ai-growth-os --filter label=com.docker.compose.service=api --format '{{.Names}}' | head -1)
docker exec -i "$API" python - <<'PY'
import base64, io, json, httpx
from PIL import Image, ImageDraw
from app.core.config import get_settings
s = get_settings()
img = Image.new('RGB', (360, 640), 'white'); d = ImageDraw.Draw(img); d.rectangle((20, 40, 340, 100), outline='black', width=4); d.ellipse((240, 450, 320, 610), outline='black', width=4)
buf = io.BytesIO(); img.save(buf, format='PNG')
url = 'data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode()
for model in ('gemini-flash-3.8',):
    body = {'model': model, 'max_tokens': 16000, 'response_format': {'type': 'json_object'},
            'messages': [{'role': 'user', 'content': [
                {'type': 'text', 'text': 'Return JSON {"regions":[{"beat_index":0,"box":[xmin,ymin,xmax,ymax]}]} for two beats: beat 0 = the wide rectangle near the top, beat 1 = the tall ellipse at bottom right. Each box is [xmin,ymin,xmax,ymax] in normalized 0..1000 image coordinates.'},
                {'type': 'image_url', 'image_url': {'url': url}}]}]}
    try:
        r = httpx.post(s.xai_base_url.rstrip('/') + '/chat/completions', json=body, headers={'Authorization': 'Bearer ' + s.xai_api_key}, timeout=120, trust_env=False)
        text = ''
        try:
            text = r.json()['choices'][0]['message']['content'] or ''
        except Exception:
            text = r.text[:200]
        print(model, 'status', r.status_code, 'len', len(text), '|', text[:200].replace('\n', ' '))
    except Exception as exc:
        print(model, 'error', type(exc).__name__)
PY
