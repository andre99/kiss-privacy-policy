#!/usr/bin/env python3
"""
Monitor Google Play keyword rankings for one or more apps (free).

Uses the unofficial google-play-scraper library (Play web endpoints).
Each app has a YAML under apps/; shared markets live in defaults.yaml.

Examples:
  python3 monitor.py --all
  python3 monitor.py --app apps/countit.yaml
  python3 monitor.py --list
  python3 monitor.py --all --render-md
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

try:
    from google_play_scraper import app as gp_app
    from google_play_scraper.constants.element import ElementSpecs
    from google_play_scraper.constants.regex import Regex
    from google_play_scraper.constants.request import Formats
    from google_play_scraper.exceptions import NotFoundError
    from google_play_scraper.utils.request import get as gp_get
except ImportError:
    print(
        "Missing dependency. Run:\n  pip3 install -r requirements.txt",
        file=sys.stderr,
    )
    sys.exit(1)

ROOT = Path(__file__).resolve().parent
DEFAULTS_PATH = ROOT / "defaults.yaml"
APPS_DIR = ROOT / "apps"
DATA_DIR = ROOT / "data"


def safe_search(
    query: str,
    n_hits: int = 30,
    lang: str = "en",
    country: str = "us",
) -> list[dict[str, Any]]:
    """
    Play search that tolerates locale-specific ds:4 layouts.

    Upstream google_play_scraper crashes with TypeError on some country/lang
    pages when the featured/top slot is missing (None).
    """
    if n_hits <= 0:
        return []

    quoted = quote(query)
    url = Formats.Searchresults.build(query=quoted, lang=lang, country=country)
    try:
        dom = gp_get(url)
    except NotFoundError:
        url = Formats.Searchresults.fallback_build(query=quoted, lang=lang)
        dom = gp_get(url)

    dataset: dict[str, Any] = {}
    for match in Regex.SCRIPT.findall(dom):
        key_match = Regex.KEY.findall(match)
        value_match = Regex.VALUE.findall(match)
        if key_match and value_match:
            dataset[key_match[0]] = json.loads(value_match[0])

    if "ds:4" not in dataset:
        return []

    cluster = dataset["ds:4"][0][1]
    top_result = None
    try:
        maybe = cluster[0][23]
        if maybe is not None:
            top_result = maybe[16]
    except (IndexError, TypeError, KeyError):
        top_result = None

    apps_dataset = None
    for idx in range(len(cluster)):
        try:
            apps_dataset = cluster[idx][22][0]
            if apps_dataset:
                break
        except (IndexError, TypeError, KeyError):
            continue
    if not apps_dataset:
        return []

    search_results: list[dict[str, Any]] = []
    if top_result:
        search_results.append(
            {
                k: spec.extract_content(top_result)
                for k, spec in ElementSpecs.SearchResultOnTop.items()
            }
        )

    n_apps = min(len(apps_dataset), n_hits)
    for app_idx in range(n_apps - len(search_results)):
        app: dict[str, Any] = {}
        for k, spec in ElementSpecs.SearchResult.items():
            app[k] = spec.extract_content(apps_dataset[app_idx])
        search_results.append(app)

    return search_results


def load_yaml(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(text) or {}
        if not isinstance(data, dict):
            raise SystemExit(f"Config must be a mapping: {path}")
        return data
    except ImportError:
        raise SystemExit("PyYAML is required. pip3 install -r requirements.txt") from None


def discover_app_configs() -> list[Path]:
    if not APPS_DIR.is_dir():
        return []
    return sorted(APPS_DIR.glob("*.yaml")) + sorted(APPS_DIR.glob("*.yml"))


def merge_config(app_path: Path, defaults: dict[str, Any] | None = None) -> dict[str, Any]:
    defaults = defaults if defaults is not None else (
        load_yaml(DEFAULTS_PATH) if DEFAULTS_PATH.exists() else {}
    )
    app = load_yaml(app_path)
    cfg = {**defaults, **app}
    slug = cfg.get("slug") or app_path.stem
    cfg["slug"] = slug
    cfg["name"] = cfg.get("name") or slug
    cfg["_config_path"] = str(app_path)
    if not cfg.get("app_id"):
        raise SystemExit(f"Missing app_id in {app_path}")
    if not cfg.get("keywords"):
        raise SystemExit(f"Missing keywords in {app_path}")
    return cfg


def out_paths(cfg: dict[str, Any]) -> dict[str, Path]:
    base = DATA_DIR / cfg["slug"]
    return {
        "dir": base,
        "csv": base / "history.csv",
        "jsonl": base / "history.jsonl",
        "md": base / "HISTORY.md",
        "html": base / "PROGRESS.html",
        "config": Path(cfg["_config_path"]),
    }


def search_rank(
    keyword: str,
    *,
    app_id: str,
    country: str,
    lang: str,
    n_hits: int,
) -> dict[str, Any]:
    checked_at = datetime.now(timezone.utc).isoformat()
    row: dict[str, Any] = {
        "checked_at": checked_at,
        "app_id": app_id,
        "keyword": keyword,
        "country": country,
        "lang": lang,
        "rank": None,
        "hits_returned": 0,
        "title_at_rank": None,
        "error": None,
    }
    try:
        results = safe_search(keyword, lang=lang, country=country, n_hits=n_hits)
    except Exception as exc:  # noqa: BLE001 — network/HTML shape changes
        row["error"] = f"{type(exc).__name__}: {exc}"
        return row

    row["hits_returned"] = len(results)
    for index, item in enumerate(results, start=1):
        if item.get("appId") == app_id:
            row["rank"] = index
            row["title_at_rank"] = item.get("title")
            break
    return row


def append_csv(path: Path, row: dict[str, Any]) -> None:
    fields = [
        "checked_at",
        "app_id",
        "keyword",
        "country",
        "lang",
        "rank",
        "hits_returned",
        "title_at_rank",
        "error",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists()
    with path.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        if not exists:
            writer.writeheader()
        writer.writerow({k: row.get(k) for k in fields})


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def print_row(row: dict[str, Any]) -> None:
    rank = row["rank"]
    rank_s = str(rank) if rank is not None else f"> {row['hits_returned']}"
    err = f"  ERROR={row['error']}" if row.get("error") else ""
    print(
        f"{row['country']}/{row['lang']}  "
        f"{rank_s:>6}  "
        f"[{row['hits_returned']:2d} hits]  "
        f"{row['keyword']}{err}"
    )


def run_monitor(cfg: dict[str, Any], *, delay_s: float) -> list[dict[str, Any]]:
    paths = out_paths(cfg)
    app_id = cfg["app_id"]
    n_hits = int(cfg.get("n_hits", 50))
    keywords = cfg.get("keywords") or []
    markets = cfg.get("markets") or [{"country": "us", "lang": "en"}]

    print(f"\n=== {cfg['name']} ({app_id}) → {paths['dir']} ===")
    try:
        info = gp_app(app_id, lang="en", country="us")
        print(f"Play title: {info.get('title')}  score={info.get('score')}")
    except Exception as exc:  # noqa: BLE001
        print(f"Warning: could not fetch app details: {exc}", file=sys.stderr)

    rows: list[dict[str, Any]] = []
    for market in markets:
        country = market["country"]
        lang = market.get("lang") or "en"
        for keyword in keywords:
            row = search_rank(
                keyword,
                app_id=app_id,
                country=country,
                lang=lang,
                n_hits=n_hits,
            )
            rows.append(row)
            print_row(row)
            append_csv(paths["csv"], row)
            append_jsonl(paths["jsonl"], row)
            time.sleep(delay_s)
    write_reports(cfg)
    return rows


def _format_rank(row: dict[str, Any]) -> str:
    rank = row.get("rank")
    if rank not in (None, ""):
        return str(rank)
    hits = row.get("hits_returned") or "?"
    if row.get("error"):
        return "error"
    return f"> {hits}"


def _load_csv_rows(csv_path: Path) -> list[dict[str, str]]:
    if not csv_path.exists():
        return []
    with csv_path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def write_history_md(cfg: dict[str, Any]) -> None:
    paths = out_paths(cfg)
    rows = _load_csv_rows(paths["csv"])
    app_id = cfg.get("app_id") or (rows[0]["app_id"] if rows else "unknown")
    name = cfg.get("name") or app_id
    keywords = cfg.get("keywords") or sorted({r["keyword"] for r in rows})
    markets = cfg.get("markets")
    if not markets:
        seen = []
        for r in rows:
            key = {"country": r["country"], "lang": r["lang"]}
            if key not in seen:
                seen.append(key)
        markets = seen

    latest: dict[tuple[str, str, str], dict[str, str]] = {}
    for row in rows:
        key = (row["keyword"], row["country"], row["lang"])
        latest[key] = row

    updated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    last_check = max((r["checked_at"] for r in latest.values()), default="—")

    lines: list[str] = [
        f"# Play keyword ranks — {name}",
        "",
        f"**App:** `{app_id}`  ",
        f"**Last monitor run rendered:** {updated}  ",
        f"**Latest check in data:** {last_check}",
        "",
        "Blank / `> N` means the app was **not** in the first ~N Play search hits "
        "(Play’s first page is usually capped around 25).",
        "",
        "## Keywords tracked",
        "",
    ]
    for kw in keywords:
        lines.append(f"- `{kw}`")
    lines += ["", "## Markets", ""]
    for m in markets:
        lines.append(f"- `{m.get('country')}` / `{m.get('lang')}`")

    lines += [
        "",
        "## Latest ranks",
        "",
        "| Market | Keyword | Rank | Hits scanned | Checked (UTC) |",
        "| --- | --- | ---: | ---: | --- |",
    ]
    for (keyword, country, lang), row in sorted(
        latest.items(),
        key=lambda item: (item[0][1], item[0][2], item[0][0]),
    ):
        checked = (row.get("checked_at") or "")[:19].replace("T", " ")
        lines.append(
            f"| `{country}/{lang}` | `{keyword}` | {_format_rank(row)} | "
            f"{row.get('hits_returned') or ''} | {checked} |"
        )

    lines += [
        "",
        "## Recent checks",
        "",
        "| When (UTC) | Market | Keyword | Rank |",
        "| --- | --- | --- | ---: |",
    ]
    for row in rows[-40:]:
        checked = (row.get("checked_at") or "")[:19].replace("T", " ")
        lines.append(
            f"| {checked} | `{row['country']}/{row['lang']}` | `{row['keyword']}` | {_format_rank(row)} |"
        )

    lines += [
        "",
        "---",
        "",
        f"Raw: [`history.csv`](history.csv) · progress: [`PROGRESS.html`](PROGRESS.html) · "
        f"config: [`{paths['config'].name}`](../../apps/{paths['config'].name})",
        "",
    ]
    paths["md"].parent.mkdir(parents=True, exist_ok=True)
    paths["md"].write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {paths['md']}")


def write_progress_html(cfg: dict[str, Any]) -> None:
    paths = out_paths(cfg)
    rows = _load_csv_rows(paths["csv"])
    app_id = cfg.get("app_id") or (rows[0]["app_id"] if rows else "unknown")
    name = cfg.get("name") or app_id
    keywords = cfg.get("keywords") or sorted({r["keyword"] for r in rows})
    payload = []
    for r in rows:
        rank_val = None
        found = r.get("rank") not in (None, "")
        is_error = bool(r.get("error"))
        if found:
            rank_val = int(r["rank"])
        elif is_error:
            rank_val = None
        elif r.get("hits_returned") not in (None, ""):
            try:
                hits_n = int(r["hits_returned"])
                rank_val = hits_n + 1 if hits_n > 0 else None
            except ValueError:
                rank_val = None
        payload.append(
            {
                "t": r.get("checked_at") or "",
                "keyword": r.get("keyword") or "",
                "market": f"{r.get('country')}/{r.get('lang')}",
                "rank": rank_val,
                "found": found,
                "error": is_error,
                "hits": int(r["hits_returned"]) if r.get("hits_returned") else 0,
            }
        )

    data_json = json.dumps(
        {"appId": app_id, "name": name, "keywords": keywords, "rows": payload},
        ensure_ascii=False,
    )
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Play keyword progress — {name}</title>
<style>
  :root {{
    --bg: #f3f3f2;
    --card: #ffffff;
    --ink: #1a1c1e;
    --muted: #5f6368;
    --green: #2e7d32;
    --line: #e0e0e0;
    --err: #c62828;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
    background: var(--bg); color: var(--ink); padding: 24px;
  }}
  h1 {{ font-size: 1.35rem; margin: 0 0 4px; color: var(--green); }}
  .sub {{ color: var(--muted); font-size: 0.9rem; margin-bottom: 20px; }}
  .panel {{
    background: var(--card); border: 1px solid var(--line); border-radius: 12px;
    padding: 16px 18px; margin-bottom: 16px;
  }}
  label {{ font-size: 0.8rem; color: var(--muted); display: block; margin-bottom: 6px; }}
  select {{
    width: min(420px, 100%); padding: 10px 12px; border-radius: 8px;
    border: 1px solid var(--line); font-size: 1rem; background: #fff;
  }}
  .hint {{ font-size: 0.85rem; color: var(--muted); margin-top: 10px; }}
  svg {{ width: 100%; height: auto; display: block; }}
  .legend {{ display: flex; flex-wrap: wrap; gap: 10px 16px; margin-top: 12px; font-size: 0.85rem; }}
  .legend span {{ display: inline-flex; align-items: center; gap: 6px; }}
  .swatch {{ width: 12px; height: 12px; border-radius: 3px; display: inline-block; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.9rem; }}
  th, td {{ text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--line); }}
  th {{ color: var(--muted); font-weight: 600; font-size: 0.75rem; text-transform: uppercase; }}
  .ok {{ color: var(--green); font-weight: 600; }}
  .miss {{ color: var(--muted); }}
  .bad {{ color: var(--err); }}
  code {{ font-size: 0.85em; }}
  a.home {{ color: var(--muted); font-size: 0.85rem; }}
</style>
</head>
<body>
  <p><a class="home" href="../">← All apps</a></p>
  <h1>{name}</h1>
  <p class="sub">App <code>{app_id}</code> · lower rank is better · values above page size mean not found in first results</p>

  <div class="panel">
    <label for="kw">Keyword</label>
    <select id="kw"></select>
    <p class="hint">Each line is a market (country/lang). Solid points = found; hollow = out of page / missing.</p>
  </div>

  <div class="panel">
    <div id="chart"></div>
    <div class="legend" id="legend"></div>
  </div>

  <div class="panel">
    <table>
      <thead>
        <tr><th>Market</th><th>Latest</th><th>Best</th><th>Checks</th><th>Errors</th></tr>
      </thead>
      <tbody id="stats"></tbody>
    </table>
  </div>

<script id="data" type="application/json">{data_json}</script>
<script>
const DATA = JSON.parse(document.getElementById('data').textContent);
const COLORS = ['#2e7d32','#1565c0','#6a1b9a','#ef6c00','#00838f','#ad1457','#455a64','#f9a825'];

const kwSelect = document.getElementById('kw');
DATA.keywords.forEach(k => {{
  const opt = document.createElement('option');
  opt.value = k; opt.textContent = k;
  kwSelect.appendChild(opt);
}});

function rowsFor(keyword) {{
  return DATA.rows.filter(r => r.keyword === keyword);
}}

function marketsFor(rows) {{
  return [...new Set(rows.map(r => r.market))].sort();
}}

function render() {{
  const keyword = kwSelect.value;
  const rows = rowsFor(keyword);
  const markets = marketsFor(rows);
  const times = [...new Set(rows.map(r => r.t))].sort();
  const dates = times.map(t => t.slice(0, 10));

  const W = 900, H = 360, pad = {{ t: 24, r: 20, b: 48, l: 44 }};
  const plotW = W - pad.l - pad.r, plotH = H - pad.t - pad.b;
  const ranks = rows.map(r => r.rank).filter(v => v != null);
  const yMax = Math.max(30, ...(ranks.length ? ranks : [30]));
  const x = i => pad.l + (times.length <= 1 ? plotW/2 : (i / (times.length - 1)) * plotW);
  const y = v => pad.t + ((v - 1) / (yMax - 1)) * plotH;

  let svg = `<svg viewBox="0 0 ${{W}} ${{H}}" role="img" aria-label="Rank over time">`;
  for (const tick of [1, 5, 10, 15, 20, 25, yMax]) {{
    if (tick > yMax) continue;
    const yy = y(tick);
    svg += `<line x1="${{pad.l}}" y1="${{yy}}" x2="${{W-pad.r}}" y2="${{yy}}" stroke="#eee"/>`;
    svg += `<text x="${{pad.l-8}}" y="${{yy+4}}" text-anchor="end" font-size="11" fill="#888">${{tick}}</text>`;
  }}
  dates.forEach((d, i) => {{
    if (times.length > 10 && i % Math.ceil(times.length/8) !== 0 && i !== times.length-1) return;
    svg += `<text x="${{x(i)}}" y="${{H-16}}" text-anchor="middle" font-size="11" fill="#888">${{d}}</text>`;
  }});
  svg += `<text x="14" y="${{pad.t + plotH/2}}" fill="#888" font-size="11" transform="rotate(-90 14 ${{pad.t + plotH/2}})">Rank (lower is better)</text>`;

  markets.forEach((m, mi) => {{
    const color = COLORS[mi % COLORS.length];
    const series = times.map(t => {{
      const hits = rows.filter(r => r.market === m && r.t === t);
      return hits.length ? hits[hits.length-1] : null;
    }});
    let path = '';
    series.forEach((pt, i) => {{
      if (!pt || pt.rank == null) return;
      const cmd = path ? 'L' : 'M';
      path += `${{cmd}}${{x(i)}} ${{y(pt.rank)}} `;
    }});
    if (path) svg += `<path d="${{path}}" fill="none" stroke="${{color}}" stroke-width="2.5"/>`;
    series.forEach((pt, i) => {{
      if (!pt || pt.rank == null) return;
      const fill = pt.found ? color : '#fff';
      const stroke = pt.error ? '#c62828' : color;
      svg += `<circle cx="${{x(i)}}" cy="${{y(pt.rank)}}" r="4.5" fill="${{fill}}" stroke="${{stroke}}" stroke-width="2"/>`;
    }});
  }});
  svg += '</svg>';
  document.getElementById('chart').innerHTML = svg;

  document.getElementById('legend').innerHTML = markets.map((m, i) =>
    `<span><i class="swatch" style="background:${{COLORS[i % COLORS.length]}}"></i>${{m}}</span>`
  ).join('');

  const tbody = document.getElementById('stats');
  tbody.innerHTML = '';
  markets.forEach(m => {{
    const mr = rows.filter(r => r.market === m);
    const found = mr.filter(r => r.found && r.rank != null);
    const latest = mr[mr.length-1];
    const best = found.length ? Math.min(...found.map(r => r.rank)) : null;
    const errors = mr.filter(r => r.error).length;
    const latestLabel = !latest ? '—'
      : latest.error ? 'error'
      : latest.found ? latest.rank
      : `> ${{latest.hits}}`;
    const tr = document.createElement('tr');
    tr.innerHTML = `<td><code>${{m}}</code></td>
      <td class="${{latest && latest.found ? 'ok' : latest && latest.error ? 'bad' : 'miss'}}">${{latestLabel}}</td>
      <td class="${{best!=null?'ok':'miss'}}">${{best ?? '—'}}</td>
      <td>${{mr.length}}</td>
      <td class="${{errors?'bad':'miss'}}">${{errors}}</td>`;
    tbody.appendChild(tr);
  }});
}}

kwSelect.addEventListener('change', render);
if (DATA.keywords.length) {{ kwSelect.value = DATA.keywords[0]; render(); }}
</script>
</body>
</html>
"""
    paths["html"].parent.mkdir(parents=True, exist_ok=True)
    paths["html"].write_text(html, encoding="utf-8")
    print(f"Wrote {paths['html']}")


def write_reports(cfg: dict[str, Any]) -> None:
    write_history_md(cfg)
    write_progress_html(cfg)


def print_summary(cfg: dict[str, Any]) -> None:
    paths = out_paths(cfg)
    rows = _load_csv_rows(paths["csv"])
    if not rows:
        print(f"No history yet for {cfg['slug']} at {paths['csv']}")
        return
    latest: dict[tuple[str, str, str], dict[str, str]] = {}
    for row in rows:
        key = (row["keyword"], row["country"], row["lang"])
        latest[key] = row
    print(f"Latest ranks — {cfg['name']} ({len(latest)} series):")
    for (keyword, country, lang), row in sorted(latest.items()):
        print(f"  {country}/{lang}  {_format_rank(row):>6}  {keyword}")


def write_pages_index(cfgs: list[dict[str, Any]], pages_root: Path) -> None:
    """Build a small Pages site: index + one folder per app with PROGRESS.html."""
    pages_root.mkdir(parents=True, exist_ok=True)
    cards = []
    for cfg in cfgs:
        paths = out_paths(cfg)
        slug = cfg["slug"]
        dest = pages_root / slug
        dest.mkdir(parents=True, exist_ok=True)
        if paths["html"].exists():
            (dest / "index.html").write_text(
                paths["html"].read_text(encoding="utf-8"),
                encoding="utf-8",
            )
        if paths["md"].exists():
            (dest / "HISTORY.md").write_text(
                paths["md"].read_text(encoding="utf-8"),
                encoding="utf-8",
            )
        cards.append(
            f'<li><a href="{slug}/"><strong>{cfg["name"]}</strong></a> '
            f'<code>{cfg["app_id"]}</code></li>'
        )

    updated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    index = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>KISS Play keyword progress</title>
<style>
  body {{
    margin: 0; font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
    background: #f3f3f2; color: #1a1c1e; padding: 32px 24px;
  }}
  h1 {{ color: #2e7d32; font-size: 1.5rem; margin: 0 0 8px; }}
  .sub {{ color: #5f6368; margin-bottom: 24px; }}
  ul {{ list-style: none; padding: 0; margin: 0; max-width: 560px; }}
  li {{
    background: #fff; border: 1px solid #e0e0e0; border-radius: 12px;
    padding: 14px 16px; margin-bottom: 10px;
  }}
  a {{ color: #1565c0; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  code {{ font-size: 0.85em; color: #5f6368; margin-left: 8px; }}
</style>
</head>
<body>
  <h1>KISS Play keyword progress</h1>
  <p class="sub">Updated {updated}. Pick an app for interactive charts.</p>
  <ul>
    {"".join(cards)}
  </ul>
</body>
</html>
"""
    (pages_root / "index.html").write_text(index, encoding="utf-8")
    print(f"Wrote Pages site → {pages_root}")


def resolve_configs(args: argparse.Namespace) -> list[dict[str, Any]]:
    defaults = load_yaml(DEFAULTS_PATH) if DEFAULTS_PATH.exists() else {}
    paths: list[Path] = []
    if args.all:
        paths = discover_app_configs()
        if not paths:
            raise SystemExit(f"No app configs in {APPS_DIR}")
    elif args.app:
        paths = [Path(p) for p in args.app]
    else:
        raise SystemExit("Pass --all or --app apps/<name>.yaml (see --list)")

    cfgs = []
    for path in paths:
        if not path.is_file():
            # Allow bare slug: countit → apps/countit.yaml
            alt = APPS_DIR / f"{path}.yaml"
            if alt.is_file():
                path = alt
            else:
                raise SystemExit(f"Config not found: {path}")
        cfg = merge_config(path, defaults)
        if args.keyword:
            cfg["keywords"] = list(
                dict.fromkeys([*(cfg.get("keywords") or []), *args.keyword])
            )
        if args.country:
            cfg["markets"] = [{"country": args.country, "lang": args.lang or "en"}]
        cfgs.append(cfg)
    return cfgs


def main() -> None:
    parser = argparse.ArgumentParser(description="Monitor Google Play keyword ranks (multi-app)")
    parser.add_argument(
        "--app",
        action="append",
        default=[],
        help="App config path or slug (repeatable). Example: --app countit",
    )
    parser.add_argument("--all", action="store_true", help="Run every YAML in apps/")
    parser.add_argument("--list", action="store_true", help="List configured apps and exit")
    parser.add_argument("--keyword", action="append", help="Extra keyword (repeatable)")
    parser.add_argument("--country", default=None, help="Override single country (e.g. us)")
    parser.add_argument("--lang", default=None, help="Override lang (e.g. en)")
    parser.add_argument("--delay", type=float, default=1.2, help="Seconds between requests")
    parser.add_argument("--summary", action="store_true", help="Show latest ranks only")
    parser.add_argument(
        "--render-md",
        action="store_true",
        help="Rebuild HISTORY.md / PROGRESS.html without scraping",
    )
    parser.add_argument(
        "--build-pages",
        action="store_true",
        help="Build aso-monitor/pages/ site from current data/ reports",
    )
    args = parser.parse_args()

    if args.list:
        for path in discover_app_configs():
            cfg = merge_config(path)
            print(f"{cfg['slug']:24} {cfg['app_id']:36} {cfg['name']}")
        return

    if not args.all and not args.app:
        # Default to all apps for convenience in CI / local
        args.all = True

    cfgs = resolve_configs(args)

    if args.render_md or args.summary or args.build_pages:
        for cfg in cfgs:
            if args.summary:
                print_summary(cfg)
            if args.render_md or args.summary or args.build_pages:
                write_reports(cfg)
        if args.build_pages or args.render_md:
            write_pages_index(cfgs, ROOT / "pages")
        return

    for cfg in cfgs:
        run_monitor(cfg, delay_s=args.delay)
    write_pages_index(cfgs, ROOT / "pages")
    print("\nDone.")


if __name__ == "__main__":
    main()
