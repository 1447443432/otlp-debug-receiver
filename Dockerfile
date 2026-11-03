FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && useradd -u 10001 -m receiver && mkdir -p /data && chown 10001:10001 /data
COPY app/ ./app/
USER 10001:10001
EXPOSE 4317 8080
VOLUME ["/data"]
CMD ["python", "-m", "app.main"]
