# RemiDex on the web (remidex.xyz): hosted/web.py runs the website, everyone's dashboards and everyone's bots.
FROM python:3.12-slim-bookworm

# Playwright, and the bots' browser with what it needs to run: Playwright's headless Chromium (lighter than Google Chrome,
# and only that one browser). REMIDEX_DATA is the server's disk: everyone's RemiDex, and what they share.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright PYTHONUNBUFFERED=1 REMIDEX_DATA=/data
RUN pip install --no-cache-dir playwright==1.60.0 \
 && playwright install --with-deps --only-shell chromium \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home remidex

WORKDIR /app
COPY . .

# (It starts as root only to hand the disk to the remidex user, and runs as that user from then on: see drop_root.)
CMD ["python3", "hosted/web.py"]
