# RemiDex

Plays BeetleBoy and BeetleCraft on RemiliaNET for you.

**Use it at [remidex.xyz](https://remidex.xyz):** sign in with your RemiliaNET account, and your own bot runs on the server. It works in any web browser, on any system: there's nothing to download or install.

Not made by Remilia.

## How it runs

One program, `hosted/web.py`, runs the website on Render (see `Dockerfile`):
- the sign-in page, and each person's own dashboard (`dashboard.py`);
- each person's bot, a short round at a time (`poke_browser.py --round`);
- one watcher of RemiliaNET's activity feed, for everyone.

Everything is kept on the server's disk (`REMIDEX_DATA`, `/data`): each person's folder in `users/`, and what everyone shares (recipe results, the activity feed) in `shared/`.

## Tests

```
python3 tests/run.py
```

They're offline, with made-up data.
