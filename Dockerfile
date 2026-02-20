FROM python:3.13-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Install Poetry
RUN pip install poetry==1.8.2

# Copy dependency files
COPY pyproject.toml ./
COPY poetry.lock* ./

# Install dependencies (no dev dependencies in production)
RUN poetry config virtualenvs.create false \
    && poetry install --no-dev --no-interaction --no-ansi

# Copy application code
COPY trader/ ./trader/
COPY frontend/dist/ ./frontend/dist/

# Expose port
EXPOSE 8080

# Run dashboard by default
CMD ["python", "-m", "trader", "serve", "--host", "0.0.0.0", "--port", "8080"]
