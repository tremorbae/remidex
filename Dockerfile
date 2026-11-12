# RemiDex on a server. For now it runs the server check (hosted/check.py), which finds out whether RemiliaNET works from
# here before the RemiDex website is built on it.
FROM python:3.12-slim-bookworm

# Playwright, and the bot's browser with what it needs to run: Playwright's headless Chromium, as on a Mac (lighter than
# Google Chrome, and only that one browser).
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright PYTHONUNBUFFERED=1 REMIDEX_HOME=/tmp/remidex-check
RUN pip install --no-cache-dir playwright==1.60.0 \
 && playwright install --with-deps --only-shell chromium \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home remidex

WORKDIR /app
COPY remidex/ remidex/
COPY hosted/ hosted/
COPY catalog.json recipes.json quests.py ./

USER remidex
CMD ["python3", "hosted/check.py"]
