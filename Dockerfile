FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml poetry.lock ./

RUN pip install --no-cache-dir poetry && \
    poetry config virtualenvs.create false && \
    poetry install --only main --no-root --no-interaction --no-ansi

COPY trader/ ./trader/

RUN mkdir -p /app/logs

CMD ["python", "-m", "trader", "bot", "--symbol", "axsusdt", "--leverage", "5"]
