# voice_decipher: voice in, drink out.
# linux/arm64 -- a Raspberry Pi 4/5 on 64-bit Raspberry Pi OS, or an
# Apple-silicon Mac. Every Python dependency ships a prebuilt arm64 wheel,
# so nothing compiles here.
FROM python:3.11-slim-bookworm

# PortAudio for the microphone (sounddevice). The only system library needed.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libportaudio2 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Bake the speech-to-text model into the image: the container then never
# needs the internet, so a dead hotspot at the venue cannot break it.
ENV HF_HOME=/models MIXMIND_STT_MODEL=tiny.en
COPY transcribe.py .
RUN python transcribe.py

COPY . .
ENV PYTHONUNBUFFERED=1 MIXMIND_PORT=/dev/serial0

# Interactive: press Enter to take the next guest. Run with -it.
CMD ["python", "pipeline.py"]
