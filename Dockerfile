FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV HOME=/home/appuser
ENV HF_HOME=/home/claude-host-home/ha-command-bridge-data/huggingface

RUN apt-get update \
    && apt-get install -y --no-install-recommends nodejs npm espeak-ng alsa-utils mpg123 openssh-client \
    && npm install -g @anthropic-ai/claude-code \
    && groupadd --gid 1000 appuser \
    && useradd --uid 1000 --gid 1000 --create-home appuser \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY CLAUDE.md ./CLAUDE.md
COPY app ./app

USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
