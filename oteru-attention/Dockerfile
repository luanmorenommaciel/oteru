FROM python:3.13-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY oteru_attention ./oteru_attention
RUN pip install --no-cache-dir . && useradd --system --uid 10001 oteru && \
    mkdir /data && chown oteru:oteru /data

USER oteru
EXPOSE 8081
ENV OTERU_ATTENTION_DB=/data/attention.sqlite3
CMD ["oteru-attention", "--host", "0.0.0.0", "--port", "8081"]
