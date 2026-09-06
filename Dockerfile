FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 TZ=UTC PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends tzdata && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml ./
COPY harness ./harness
RUN pip install .
USER nobody
ENTRYPOINT ["harness"]
