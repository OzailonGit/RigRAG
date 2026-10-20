# Built from the repo root (docker build -f backend/Dockerfile .), since the
# app reads data/reference and data/seed from the repo root, which is / here.
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

# CPU-only PyTorch for the embedding model: the default wheel bundles CUDA,
# which the container never uses and which adds gigabytes to the image.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ .
COPY data/reference /data/reference
COPY data/seed /data/seed

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
