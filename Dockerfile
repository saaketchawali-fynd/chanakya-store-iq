FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TZ=Asia/Kolkata
RUN apt-get update && apt-get install -y --no-install-recommends \
      ffmpeg fonts-noto-core fonts-noto-color-emoji libraqm0 curl && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir uv
WORKDIR /app
COPY pyproject.toml uv.lock* ./
RUN uv pip install --system -r pyproject.toml
RUN python -m playwright install --with-deps chromium
COPY . .
RUN uv pip install --system --no-deps -e . \
 && python scripts/make_product_images.py && python scripts/make_screen_image.py
EXPOSE 8080
HEALTHCHECK CMD curl -fs http://localhost:${PORT:-8080}/health || exit 1
CMD ["sh", "-c", "uvicorn chanakya.app.main:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1"]
