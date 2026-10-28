# chain-api: web3.py over HTTP
#
# Build:  docker build -t chain-api .
# Run:    docker run --rm -p 8000:8000 chain-api
#
# Python 3.13 (not 3.15 like the notebooks): ckzg, a web3.py dependency, has no
# prebuilt Linux wheel for 3.15 yet, and compiling it would need a C toolchain.

# 1. Base image: a minimal Debian with Python already installed
FROM python:3.13-slim

# 2. Do not write .pyc files, and print logs immediately (no buffering)
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# 3. Dependencies first: this layer is cached and only rebuilt when requirements.txt changes
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 4. Then the code: editing app.py only rebuilds from here
COPY app.py .

# 5. Do not run as root inside the container
RUN useradd --create-home appuser
USER appuser

# 6. Configuration comes from the environment at `docker run` time, never baked into the image.
#    This is only the default (public Polygon node, no API key).
ENV RPC_URL=https://polygon-bor-rpc.publicnode.com

# 7. Documents the port the app listens on (publishing it is `docker run -p`)
EXPOSE 8000

# 8. The process the container runs: the API server
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
