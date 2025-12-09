# Multi-stage not strictly needed for Python, but using a build stage for pip caching
FROM python:3.10-slim AS builder
WORKDIR /app
COPY requirements.txt .
RUN pip install --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

FROM python:3.10-slim
WORKDIR /app
# copy installed packages
COPY --from=builder /usr/local/lib/python3.10/site-packages /usr/local/lib/python3.10/site-packages
COPY . /app
RUN adduser --disabled-password --gecos "" appuser && chown -R appuser /app
USER appuser
ENV APP_VERSION="v1.0.0"
EXPOSE 80
CMD ["gunicorn", "--bind", "0.0.0.0:80", "app:create_app()", "--workers", "2", "--timeout", "30"]