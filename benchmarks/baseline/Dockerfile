# Plain-proxy baseline image (AGW-BENCH). Minimal on purpose.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app

RUN useradd --create-home --uid 1000 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 9000

CMD ["uvicorn", "baseline_proxy:app", "--host", "0.0.0.0", "--port", "9000"]
