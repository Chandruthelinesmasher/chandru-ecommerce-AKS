# ---------- BUILD STAGE ----------
FROM python:3.10-slim AS builder

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt


# ---------- RUNTIME STAGE ----------
FROM python:3.10-slim

WORKDIR /app

# Copy installed packages & binaries from builder
COPY --from=builder /usr/local/lib/python3.10/site-packages /usr/local/lib/python3.10/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy entire app (Flask app + templates + static)
COPY . /app

# Create non-root user
RUN adduser --disabled-password --gecos "" appuser && chown -R appuser /app
USER appuser

# Application version
ENV APP_VERSION="v1.0.0"

# Correct HTTP port
EXPOSE 8000

# ---------- RUN APPLICATION ----------
# For Flask inside Gunicorn:
#  app:app = filename:flask_app_variable
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "app:create_app()", "--workers", "2", "--timeout", "60"]

