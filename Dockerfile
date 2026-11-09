# SuperDuperRaidBot in a container: for a Raspberry Pi (64-bit), a home server or a VPS. See DOCKER.md.
#
# Nothing private goes into the image: the token and settings come from .env at run time (docker-compose.yml's
# env_file), and raidbot.db, its backups and raidbot.log live in the ./data folder, mounted at /data.
# python:3.12-slim is published for amd64 and arm64, so the same file builds on a PC and on a Pi 3/4/5. It is pinned
# by digest (and the packages by constraints.txt) so every build is the one CI tested; Dependabot moves both.
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    RAIDBOT_DOCKER=1 \
    DB_PATH=/data/raidbot.db \
    LOG_DIR=/data

WORKDIR /app

# Dependencies first, so a code-only update doesn't reinstall them.
COPY requirements.txt constraints.txt ./
RUN pip install -r requirements.txt -c constraints.txt

# Run as an ordinary user (uid 1000, the first user on Raspberry Pi OS), never as root.
RUN useradd --uid 1000 --user-group --no-create-home --shell /usr/sbin/nologin raidbot \
    && mkdir -p /data /app/icons \
    && chown raidbot:raidbot /data

COPY bot.py checks.py db.py embeds.py wow_data.py ./

USER raidbot
VOLUME ["/data"]
# Healthy while the scheduler runs: it touches /data/.heartbeat every minute. Shows in `docker ps`.
HEALTHCHECK --interval=2m --timeout=10s --start-period=3m --retries=3 \
    CMD ["python", "-c", "import os, sys, time; sys.exit(time.time() - os.path.getmtime('/data/.heartbeat') > 300)"]
CMD ["python", "bot.py"]
