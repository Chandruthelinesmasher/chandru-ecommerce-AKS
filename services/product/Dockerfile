# ---------- BUILD STAGE ----------
FROM python:3.10-slim AS builder

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ---------- RUNTIME STAGE ----------
FROM python:3.10-slim

WORKDIR /app

# Copy dependencies
COPY --from=builder /usr/local/lib/python3.10/site-packages /usr/local/lib/python3.10/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy app
COPY . /app

# Create non-root user
RUN adduser --disabled-password --gecos "" appuser && chown -R appuser /app
USER appuser

EXPOSE 8000

# ---------- FIXED ENTRYPOINT ----------
CMD ["gunicorn", "app:create_app()", "--bind", "0.0.0.0:8000", "--workers", "2", "--timeout", "60"]
