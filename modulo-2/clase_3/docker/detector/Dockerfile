# detector: looks for arbitrage cycles in every block stored by the collector (runs forever)
FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY detector.py .
RUN useradd --create-home appuser
USER appuser
CMD ["python", "detector.py"]
