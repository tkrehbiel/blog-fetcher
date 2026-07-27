# Blog Feed Activity Tracker

A portable Python utility designed to efficiently monitor and report writing activity across multiple blog feeds during event-based writing challenges (such as Blaugust).

It parses a list of feeds from an OPML configuration (either local or online), fetches their contents in parallel, caches headers to avoid redundant downloads, and creates a report highlighting updates starting from a given date.

## Features

- **Multi-Format Support**: Natively parses RSS (XML), Atom (XML), and JSON Feed standards.
- **High Performance**: Fetches all feeds concurrently using Python's thread-pool executor.
- **HTTP Conditional GET (Caching)**: Uses `ETag` and `Last-Modified` headers to fetch only modified feeds (receiving `304 Not Modified` with zero network body transfer).
- **Persistent Cache Merging**: Avoids losing posts that roll off short-history feeds. Newly fetched posts are incrementally merged and deduplicated (via GUID or URL) with previously cached entries.
- **Robust Feed Handling**: Relies on standard library modules and the battle-tested `feedparser` library to handle RSS, Atom, and JSON feeds safely (including edge cases, encodings, and date formats).
- **Rich Reports**: Generates detailed GFM Markdown summaries or a sleek, responsive, modern glassmorphic HTML page featuring dark-mode styling, real-time client-side search, and interactive sorting.

---

## Installation & Requirements

Ensure you have **Python 3.11** or newer installed.

Simply clone or copy the folder into your project, install dependencies from `requirements.txt`, make the script executable, and run it:

```bash
pip install -r requirements.txt
chmod +x blog_fetcher.py
```

---

## Usage

Run the script by passing an OPML file and a start date:

```bash
./blog_fetcher.py --opml tests.opml --since 2026-08-01 --format html --output report.html
```

### CLI Arguments

| Argument | Description | Default |
| :--- | :--- | :--- |
| `--opml` | Path or URL to the input OPML file | `tests.opml` (in the same directory) |
| `--since` | Filter posts starting from date (`YYYY-MM-DD`) | First day of the current month |
| `--format` | Output report format: `markdown` or `html` | `markdown` |
| `--output` | Save report to a file path. If omitted, no report is generated | None |
| `--cache` | File path to load/save feed cache | `feed_cache.json` (in the same directory) |
| `--concurrency` | Maximum number of parallel workers to download feeds | `15` |
| `--ignore-ssl-errors` | Bypasses local SSL chain verification (helps on macOS Python installations) | `True` |

---

## Caching & Merging Internals

To make repeated checks fast and lightweight, the tracker implements two layers of caching inside `feed_cache.json`:

1. **Conditional GET Cache**:
   During execution, the script checks the cache for `etag` or `last_modified` fields. It adds `If-None-Match` and `If-Modified-Since` headers to the HTTP request. If the server supports conditional headers and returns a `304 Not Modified`, the script skips parsing and uses the cached post database.

2. **Deduplicating Merge Cache**:
   Many blogs only syndicate the latest 10 to 20 posts. High-frequency authors will roll off older posts during a month-long event. 
   When a feed is successfully parsed (HTTP 200), the tracker merges the new items with the items already saved in the cache. It keys posts by their `guid` (or link URL if a GUID is unavailable) to deduplicate them, meaning you'll never lose older articles.

---

## Output Examples

### HTML Report
Generates a highly styled, modern dashboard with:
- Stats counters showing Blogs Checked, Blogs with Updates, and Total New Posts.
- Search filter to instantly search blogs by title.
- Buttons to toggle between showing "All Blogs" or "Updated Only".
- Expanding sections containing the list of new posts, their links, and publication dates.

### Markdown Report
Outputs a neat GFM table:
```markdown
# Blog Feed Activity Report
**Since Date:** `2026-07-01 00:00:00 UTC`
**Generated At:** `2026-07-27 07:38:00 UTC`

## Feed Activity Summary

| Blog Title | New Posts | Status | Last Checked |
| :--- | :---: | :---: | :--- |
| [Endgame Viable](https://endgameviable.com/) | **20** | ✅ 200 Refreshed | 2026-07-27 07:38:00 |
| [Daring Fireball](https://daringfireball.net/) | **38** | ✅ 200 Refreshed | 2026-07-27 07:38:00 |
```

---

## GitHub Pages & GitHub Actions Deployment

You can host this tool as a public feed dashboard using GitHub Pages. It runs automatically in the cloud on a cron schedule to fetch updates, commits the cache changes back to git, and republishes the static site.

### Setup Instructions

1. **Enable GitHub Pages**:
   - Go to your repository settings on GitHub.
   - Under **Pages** (under the Code and automation section), set the Build and deployment source to **GitHub Actions**.
2. **Configure Workflow Permissions**:
   - Go to **Settings** -> **Actions** -> **General**.
   - Under **Workflow permissions**, select **Read and write permissions** (this is required to commit updated caching headers back to `main`).
3. **Commit & Push**:
   - Push `.github/workflows/deploy.yml` and the updated `Makefile` to your `main` branch.
   - The initial deploy workflow will trigger automatically on the push. Once completed, your site will be public at `https://<username>.github.io/<repository-name>/`.

