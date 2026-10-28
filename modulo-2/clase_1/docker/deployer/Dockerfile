# deployer: deploys QuantToken to anvil and makes a few transfers (one-shot), then exits.
# Also used for manual transfers:  docker compose run --rm deployer python transfer.py --to 2 --amount 3
FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY QuantToken.json deploy.py transfer.py ./
CMD ["python", "deploy.py"]
