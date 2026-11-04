# collector: reads the pools of Polygon on every new block and stores them in Postgres (runs forever)
FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY collector.py .
RUN useradd --create-home appuser
USER appuser
CMD ["python", "collector.py"]
