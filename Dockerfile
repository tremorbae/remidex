# RemiDex on a server. For now it runs the server check (hosted/check.py), which finds out whether RemiliaNET works from
# here before the RemiDex website is built on it.
FROM mcr.microsoft.com/playwright/python:v1.60.0-noble

# Google Chrome itself: the bot uses Chrome, as on a Mac. (The image already has Playwright 1.60.0 and its own Chromium.)
RUN python3 -m playwright install --with-deps chrome

ENV PYTHONUNBUFFERED=1 REMIDEX_HOME=/tmp/remidex-check
WORKDIR /app
COPY remidex/ remidex/
COPY hosted/ hosted/
COPY catalog.json recipes.json quests.py ./

USER pwuser
CMD ["python3", "hosted/check.py"]
