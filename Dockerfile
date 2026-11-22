# RemiDex on the web (remidex.xyz): hosted/web.py runs the website, everyone's dashboards and everyone's bots.
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 REMIDEX_DATA=/data
RUN pip install --no-cache-dir psycopg2-binary \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home remidex

WORKDIR /app
COPY . .

# (It starts as root only to hand the disk to the remidex user, and runs as that user from then on: see drop_root.)
CMD ["python3", "hosted/web.py"]
