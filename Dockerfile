# RemiDex on a server. For now it runs the server check (hosted/check.py), which finds out whether RemiliaNET works from
# here before the RemiDex website is built on it.
FROM mcr.microsoft.com/playwright/python:v1.60.0-noble

# The bot's browser: Playwright's headless Chromium, as on a Mac (lighter than Google Chrome). The image already has
# Playwright 1.60.0 and its browsers, so this only makes sure.
RUN python3 -m playwright install --only-shell chromium

ENV PYTHONUNBUFFERED=1 REMIDEX_HOME=/tmp/remidex-check
WORKDIR /app
COPY remidex/ remidex/
COPY hosted/ hosted/
COPY catalog.json recipes.json quests.py ./

USER pwuser
CMD ["python3", "hosted/check.py"]
