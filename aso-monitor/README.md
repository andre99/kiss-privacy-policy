# Play keyword rank monitor (multi-app)

Tracks Google Play search ranks for every app under `apps/`. Free, unofficial Play web endpoints via `google-play-scraper`.

## Apps

| Slug | Package | Config |
| --- | --- | --- |
| `gratitude-journal` | `com.kiss.gratitudeJournal` | [`apps/gratitude-journal.yaml`](apps/gratitude-journal.yaml) |
| `countit` | `com.kiss.countit` | [`apps/countit.yaml`](apps/countit.yaml) |
| `xylo-hero` | `com.kiss.xylo` | [`apps/xylo-hero.yaml`](apps/xylo-hero.yaml) |

Shared markets / `n_hits`: [`defaults.yaml`](defaults.yaml).

### Add another app

1. Create `apps/<slug>.yaml` with at least `name`, `app_id`, and `keywords`.
2. Re-run the Action (or `python3 monitor.py --all`).

Optional keys: `slug` (defaults to filename), `markets`, `n_hits` (override defaults).

## Local

```bash
cd aso-monitor
python3 -m pip install -r requirements.txt
python3 monitor.py --list
python3 monitor.py --all                 # all apps
python3 monitor.py --app countit         # one app
open pages/index.html                    # after a run
```

## GitHub Action

Workflow: `.github/workflows/play-keyword-monitor.yml`

- Daily **08:15 UTC** + manual **Run workflow**
- Restores `aso-monitor-history` artifact → appends → uploads again
- Publishes interactive charts to **GitHub Pages** (`gh-pages`)
- Updates issue **Play keyword progress (ASO)** (subscribe for notifications)

### One-time: enable Pages

After the first successful run creates `gh-pages`:

1. **Settings → Pages**
2. Source: **Deploy from a branch**
3. Branch: **`gh-pages`** / **`/`**

Charts: `https://andre99.github.io/kiss-privacy-policy/`

## Output layout

```
data/<slug>/history.csv
data/<slug>/HISTORY.md
data/<slug>/PROGRESS.html
pages/index.html          # hub
pages/<slug>/index.html   # chart (published)
```

`> N` / blank rank = not in the first N Play hits (usually the whole first page, ~25).
