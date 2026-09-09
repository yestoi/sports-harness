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
# Two lines, deliberately, and never one with both sources: given several sources and a
# directory destination, Docker copies the *contents* of a source directory, so the one-line
# form would put env.py, script.py.mako and versions/ straight into /app and break both
# `script_location` and the /app/migrations fallback in harness/db/migrate.py. The line above
# works only because its destination names the directory.
COPY alembic.ini ./
COPY migrations ./migrations
RUN pip install -c constraints.txt .
USER nobody
ENTRYPOINT ["harness"]
