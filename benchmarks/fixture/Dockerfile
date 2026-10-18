FROM python:3.11-slim
WORKDIR /app
COPY benchmarks/baseline/requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY benchmarks/fixture/app.py app.py
COPY benchmarks/fixture/contract.py contract.py
USER 1000:1000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "9100", "--no-proxy-headers"]
