# oteru-ingestor, Python implementation (#24). Build context: oteru-ingestor/
# (so the shared schema.sql is in reach):
#   docker build -f python/Dockerfile -t oteru-ingestor-python oteru-ingestor
FROM python:3.13-slim
WORKDIR /app
COPY python/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY schema.sql python/ingestor.py ./
ENV PORT=4318 INGEST_DB=ingest_python PYTHONUNBUFFERED=1
EXPOSE 4318
USER nobody
CMD ["python", "ingestor.py"]
