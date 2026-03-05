FROM python:3.13-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Install Poetry (2.x supports PEP 621 [project] metadata in pyproject.toml)
RUN pip install poetry==2.1.3

# Copy dependency files
COPY pyproject.toml ./
COPY poetry.lock* ./

# Install dependencies (main group only, no dev dependencies in production)
RUN poetry config virtualenvs.create false \
    && poetry install --only main --no-root --no-interaction --no-ansi

# Copy application code
COPY trader/ ./trader/
COPY db/ ./db/
COPY frontend/dist/ ./frontend/dist/

# Expose port
EXPOSE 8080

# Run dashboard by default
CMD ["python", "-m", "trader", "serve", "--host", "0.0.0.0", "--port", "8080"]
