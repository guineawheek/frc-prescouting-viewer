# team division viewer

enter divison code, click on team, get video

this was an experiment in how good claude was at making stupid applications i didn't want to write by hand.
it did a passable job, although it lacks key instincts valuable for good human UI/UX or efficient querying of APIs for good repsonse times.

and they say that coding is a dead career. seems plenty alive to me.

for a writeup, see [this chiefdelphi post](https://www.chiefdelphi.com/t/4322-clockwork-2026-build-thread-open-alliance/511196/192) on this app

## requirements

* an install of [uv](https://docs.astral.sh/uv/)

## running

make `tba_api.json` and populate it as such:

```json
{
    "key": "<insert TBA API key here>"
}
```

```bash
uv run python3 app.py
```

open `http://localhost:5000`

## things are screwed up?

try deleting `epa_cache.db` and restart

## license

0bsd. i don't care.