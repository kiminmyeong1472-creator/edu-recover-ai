FROM python:3.12-slim
ARG INSTALL_LOCAL_SIGN=false
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg tesseract-ocr tesseract-ocr-kor tesseract-ocr-eng liblouis-bin liblouis-data libglib2.0-0 libgl1 \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
COPY deployment/accessibility/requirements-local-sign.txt /tmp/requirements-local-sign.txt
RUN pip install --no-cache-dir -r requirements.txt \
    && if [ "$INSTALL_LOCAL_SIGN" = "true" ]; then pip install --no-cache-dir -r /tmp/requirements-local-sign.txt; fi
COPY . .
# The operator supplies a checked local .task asset; no model is downloaded here.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
