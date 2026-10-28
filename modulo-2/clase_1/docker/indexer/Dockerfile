# indexer: stores every block and ERC-20 Transfer of the chain in Postgres (runs forever)
FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY indexer.py .
RUN useradd --create-home appuser
USER appuser
CMD ["python", "indexer.py"]
