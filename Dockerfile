FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 TZ=UTC PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends tzdata && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml ./
# Exact pins for every transitive dependency, so this build installs the set the suite was run
# against instead of resolving fresh from the lower bounds in pyproject.toml. Regenerating
# constraints.txt is a gate for the autopilot loop, never a ruling.
COPY constraints.txt ./
COPY harness ./harness
RUN pip install -c constraints.txt .
USER nobody
ENTRYPOINT ["harness"]
