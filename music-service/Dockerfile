FROM python:3.11-slim

# ffmpeg for transcoding any audio source to PCM, libopus for the opus encoder.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libopus0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY music_service.py .

# 8800: control API + device websocket endpoint
EXPOSE 8800

CMD ["python", "music_service.py", "--host", "0.0.0.0", "--port", "8800", "--music-dir", "/music"]
