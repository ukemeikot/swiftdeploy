FROM python:3.12-alpine

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN addgroup -S appgroup && adduser -S -G appgroup -u 10001 appuser

COPY app/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ .

USER 10001:10001

EXPOSE 3000

# Single worker + threads so the in-memory chaos state is shared across all requests.
CMD ["gunicorn", "--bind", "0.0.0.0:3000", "--workers", "1", "--threads", "4", "--access-logfile", "-", "main:app"]
