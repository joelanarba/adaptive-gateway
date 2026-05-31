# Adaptive API Gateway — application image
# Build context is ./gateway, so /app contains main.py, config.py, models/, etc.
FROM python:3.11-slim

# Predictable, log-friendly Python behaviour inside containers.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Install dependencies first so this layer is cached when only app code changes.
# The slim image ships manylinux wheels for asyncpg and bcrypt, so no compiler
# or libpq headers are required at build time.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application source.
COPY . /app

# Run as an unprivileged user.
RUN useradd --create-home --uid 1000 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# Liveness probe using only the Python standard library (no curl needed).
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request, sys; \
sys.exit(0) if urllib.request.urlopen('http://localhost:8000/health', timeout=4).status == 200 else sys.exit(1)"

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
