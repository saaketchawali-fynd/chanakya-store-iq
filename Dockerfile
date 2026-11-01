FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TZ=Asia/Kolkata
# glibc: two malloc arenas (not one per thread), and big buffers (photos, drawings) handed back to the OS when freed,
# so memory falls back after a try-on instead of staying at its peak on the 512 MB instance
ENV MALLOC_ARENA_MAX=2 MALLOC_MMAP_THRESHOLD_=131072 MALLOC_TRIM_THRESHOLD_=131072
RUN apt-get update && apt-get install -y --no-install-recommends \
      ffmpeg fonts-noto-core fonts-noto-color-emoji libraqm0 libglib2.0-0 curl && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir uv
WORKDIR /app
COPY pyproject.toml uv.lock* ./
RUN uv pip install --system -r pyproject.toml
RUN python -m playwright install --with-deps chromium
COPY . .
# product images, screen wallpaper and clips are committed (regenerate with scripts/make_*.py)
RUN uv pip install --system --no-deps -e .
EXPOSE 8080
HEALTHCHECK CMD curl -fs http://localhost:${PORT:-8080}/health || exit 1
CMD ["sh", "-c", "uvicorn chanakya.app.main:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1"]
