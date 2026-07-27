#!/usr/bin/env python3
"""
Blog Feed Tracker
Fetches RSS, Atom, and JSON feeds from an OPML file or URL,
caches them using HTTP Conditional GET, and generates activity reports.
"""

import argparse
import concurrent.futures
import json
import os
import socket
import ssl
import sys
import urllib.request

# Set default timeout for all socket connections (including feedparser)
socket.setdefaulttimeout(10)
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
import email.utils
import yaml

# Default files
DEFAULT_OPML = os.path.join(os.path.dirname(__file__), "tests.opml")
DEFAULT_CACHE = os.path.join(os.path.dirname(__file__), "feed_cache.json")
DEFAULT_EXCLUDE = os.path.join(os.path.dirname(__file__), "exclude.yaml")
USER_AGENT = "Endgame Viable's Blog Fetcher/1.0 (Python) (https://endgameviable.com/page/blog-fetcher/)"

import feedparser

def parse_datetime(date_str):
    """
    Robust datetime parsing to handle RFC 822 (RSS) and ISO 8601 (Atom/JSON).
    Normalizes the output to a timezone-aware UTC datetime.
    """
    if not date_str:
        return None
    date_str = date_str.strip()

    # 1. Try RFC 822 (RSS format: "Sun, 26 Jul 2026 15:57:44 GMT")
    try:
        dt = email.utils.parsedate_to_datetime(date_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        pass

    # 2. Try ISO 8601 (Atom / JSON Feed: "2026-07-26T15:57:44Z" or "2026-07-26T15:57:44.123-04:00")
    try:
        val = date_str
        if val.endswith('Z'):
            val = val[:-1] + '+00:00'
        dt = datetime.fromisoformat(val)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        pass

    # 3. Fallback common string patterns
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S%z", "%Y-%m-%d", "%a, %d %b %Y %H:%M:%S"):
        try:
            dt = datetime.strptime(date_str, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            pass

    return None

def load_opml(path_or_url, context=None):
    """Loads OPML XML content from a local file path or a HTTP(S) URL."""
    if path_or_url.startswith(('http://', 'https://')):
        req = urllib.request.Request(
            path_or_url,
            headers={'User-Agent': USER_AGENT}
        )
        with urllib.request.urlopen(req, timeout=15, context=context) as response:
            return response.read()
    else:
        with open(path_or_url, 'rb') as f:
            return f.read()

def parse_opml(opml_content):
    """Parses OPML XML content and extracts outline items that specify a feed URL."""
    root = ET.fromstring(opml_content)
    feeds = []
    # Find all outline elements recursively
    for outline in root.findall('.//outline'):
        xml_url = outline.attrib.get('xmlUrl')
        if xml_url:
            title = outline.attrib.get('title') or outline.attrib.get('text') or 'Untitled Feed'
            html_url = outline.attrib.get('htmlUrl', '')
            feeds.append({
                'title': title.strip(),
                'xml_url': xml_url.strip(),
                'html_url': html_url.strip()
            })
    return feeds

def merge_posts(cached_posts, new_posts):
    """
    Deduplicates and merges posts using GUID or permalink link.
    Sorts the output chronologically (ascending).
    """
    merged = {}
    
    # Add previously cached posts first
    for p in cached_posts:
        key = p.get('guid') or p.get('link')
        if key:
            merged[key] = p

    # Add/overwrite with newly parsed posts
    for p in new_posts:
        key = p.get('guid') or p.get('link')
        if key:
            merged[key] = p

    # Sort merged posts by date ascending (invalid dates go to the bottom)
    def get_sort_timestamp(post):
        dt = parse_datetime(post.get('pub_date'))
        return dt.timestamp() if dt else 0.0

    return sorted(list(merged.values()), key=get_sort_timestamp)

def fetch_single_feed(feed, cache_entry, context=None):
    """
    Fetches and parses a single feed (XML or JSON) with HTTP Conditional GET support.
    Returns the updated cache entry representing this feed.
    """
    xml_url = feed['xml_url']
    cached_etag = cache_entry.get('etag')
    cached_last_modified = cache_entry.get('last_modified')
    cached_posts = cache_entry.get('posts', [])

    result = {
        'title': feed['title'],
        'html_url': feed['html_url'],
        'etag': cached_etag,
        'last_modified': cached_last_modified,
        'last_checked': datetime.now(timezone.utc).isoformat(),
        'status': 200,
        'posts': cached_posts,
        'new_posts_count': 0,
        'updated_this_run': False,
        'error_msg': None
    }

    try:
        # Build Request with custom headers and conditional GET fields
        req = urllib.request.Request(xml_url, headers={'User-Agent': USER_AGENT})
        if cached_etag:
            req.add_header('If-None-Match', cached_etag)
        if cached_last_modified:
            req.add_header('If-Modified-Since', cached_last_modified)

        # Execute network query
        status = 200
        content_bytes = b""
        response_headers = {}
        try:
            with urllib.request.urlopen(req, timeout=10, context=context) as response:
                status = response.status
                content_bytes = response.read()
                response_headers = response.info()
        except urllib.error.HTTPError as e:
            status = e.code
            if status != 304:
                raise e

        result['status'] = status

        if status == 304:
            # Not Modified: Keep cached values
            pass
        elif status in (200, 301, 302, 307):
            result['updated_this_run'] = True
            result['etag'] = response_headers.get('ETag')
            result['last_modified'] = response_headers.get('Last-Modified')

            content_type = response_headers.get('Content-Type', '').lower()
            new_posts = []

            if 'json' in content_type or xml_url.endswith('.json'):
                # Parse as JSON Feed
                feed_data = json.loads(content_bytes.decode('utf-8', errors='ignore'))
                items = feed_data.get('items', [])
                for item in items:
                    title = item.get('title', 'Untitled Post').strip()
                    link = item.get('url', '').strip()
                    guid = item.get('id', link).strip()

                    raw_date = item.get('date_published') or item.get('date_modified', '')
                    pub_dt = parse_datetime(raw_date)

                    new_posts.append({
                        'title': title,
                        'link': link,
                        'pub_date': pub_dt.isoformat() if pub_dt else '',
                        'guid': guid
                    })
            else:
                # Parse as XML Feed (RSS/Atom)
                parsed = feedparser.parse(content_bytes)
                if getattr(parsed, 'bozo_exception', None) and not getattr(parsed, 'entries', None):
                    raise Exception(f"XML Parsing Error: {parsed.bozo_exception}")

                for entry in getattr(parsed, 'entries', []):
                    title = entry.get('title', 'Untitled Post').strip()
                    link = entry.get('link', '').strip()
                    guid = entry.get('id', link).strip()

                    pub_dt = None
                    parsed_time = entry.get('published_parsed') or entry.get('updated_parsed')
                    if parsed_time:
                        try:
                            pub_dt = datetime(*parsed_time[:6], tzinfo=timezone.utc)
                        except Exception:
                            pass

                    if not pub_dt:
                        raw_date = entry.get('published') or entry.get('updated', '')
                        pub_dt = parse_datetime(raw_date)

                    new_posts.append({
                        'title': title,
                        'link': link,
                        'pub_date': pub_dt.isoformat() if pub_dt else '',
                        'guid': guid
                    })

            # Merge and deduplicate with existing cache
            result['posts'] = merge_posts(cached_posts, new_posts)

            # Calculate new posts since last run
            cached_guids = {p.get('guid') or p.get('link') for p in cached_posts}
            new_since_last_run = [p for p in new_posts if (p.get('guid') or p.get('link')) not in cached_guids]
            result['new_posts_count'] = len(new_since_last_run)
        else:
            result['error_msg'] = f"HTTP {status}"

    except Exception as e:
        result['status'] = -1
        result['error_msg'] = str(e)

    return xml_url, result

def fetch_all_feeds(feeds, cache, concurrency=15, ignore_ssl=True):
    """Fetches all feeds concurrently using ThreadPoolExecutor."""
    context = None
    if ignore_ssl:
        context = ssl._create_unverified_context()

    updated_cache = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
        # Submit tasks
        future_to_url = {}
        for feed in feeds:
            url = feed['xml_url']
            cache_entry = cache.get(url, {})
            # Ensure feed info is synchronized
            cache_entry['title'] = feed['title']
            cache_entry['html_url'] = feed['html_url']
            
            future = executor.submit(fetch_single_feed, feed, cache_entry, context)
            future_to_url[future] = url

        # Process results as they complete
        for future in concurrent.futures.as_completed(future_to_url):
            url = future_to_url[future]
            try:
                xml_url, result = future.result()
                updated_cache[xml_url] = result
                
                title = result['title']
                status = result['status']
                if status == 304:
                    print(f"  {title}: No updates since the last call")
                elif status == 200:
                    print(f"  {title}: Fetching modified feed (updated)")
                elif result.get('error_msg'):
                    print(f"  {title}: Error fetching - {result['error_msg']}")
                else:
                    print(f"  {title}: Fetched (HTTP {status})")
            except Exception as e:
                title = cache.get(url, {}).get('title', 'Unknown')
                print(f"  {title}: Error fetching - Thread Error: {e}")
                updated_cache[url] = {
                    'title': title,
                    'html_url': cache.get(url, {}).get('html_url', ''),
                    'etag': cache.get(url, {}).get('etag'),
                    'last_modified': cache.get(url, {}).get('last_modified'),
                    'last_checked': datetime.now(timezone.utc).isoformat(),
                    'status': -1,
                    'posts': cache.get(url, {}).get('posts', []),
                    'updated_this_run': False,
                    'error_msg': f"Thread Error: {e}"
                }
    return updated_cache

def get_latest_post_timestamp(info):
    """Finds the timestamp of the latest post in the feed info (0.0 if none)."""
    latest_dt = None
    for post in info.get('posts', []):
        dt = parse_datetime(post.get('pub_date'))
        if dt:
            if latest_dt is None or dt > latest_dt:
                latest_dt = dt
    return latest_dt.timestamp() if latest_dt else 0.0

def generate_markdown(cache, since_dt, last_run_dt=None):
    """Generates a Markdown/GFM report detailing new posts since given date."""
    lines = []
    lines.append(f"# Blog Feed Activity Report")
    lines.append(f"**Since Date:** `{since_dt.strftime('%Y-%m-%d %H:%M:%S UTC')}`  ")
    lines.append(f"**Generated At:** `{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}`\n")
    
    # Summary Table
    lines.append("## Feed Activity Summary\n")
    lines.append("| Blog Title | Posts | Status | Last Update |")
    lines.append("| :--- | :---: | :---: | :--- |")
    
    blog_details = []
    total_new_posts = 0
    updated_blogs_count = 0
    
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    updated_today_count = 0
    new_posts_today_count = 0
    
    # Process each feed sorted by latest update date descending, then alphabetically by title
    sorted_feeds = sorted(
        cache.items(),
        key=lambda x: (-get_latest_post_timestamp(x[1]), x[1]['title'].lower())
    )
    for xml_url, info in sorted_feeds:
        title = info['title']
        html_url = info['html_url']
        status_code = info['status']
        last_checked = info.get('last_checked', 'Never')
        error = info.get('error_msg')
        
        # Filter posts since date
        new_posts = []
        for post in info.get('posts', []):
            dt = parse_datetime(post.get('pub_date'))
            if dt and dt >= since_dt:
                new_posts.append(post)
                
        post_count = len(new_posts)
        total_new_posts += post_count
        if post_count > 0:
            updated_blogs_count += 1

        # Count today's updates
        has_post_today = False
        for post in info.get('posts', []):
            dt = parse_datetime(post.get('pub_date'))
            if dt and dt >= today_start:
                new_posts_today_count += 1
                has_post_today = True
        if has_post_today:
            updated_today_count += 1

        # Format status indicator
        if error:
            status_str = f"❌ Error ({status_code})"
        elif status_code == 304:
            status_str = "💤 304 Cached"
        elif status_code == 200:
            status_str = "✅ 200 Refreshed"
        else:
            status_str = f"❓ HTTP {status_code}"
            
        blog_link = f"[{title}]({html_url})" if html_url else title
        # Count posts today for this specific feed
        feed_today_count = 0
        for post in info.get('posts', []):
            dt = parse_datetime(post.get('pub_date'))
            if dt and dt >= today_start:
                feed_today_count += 1

        posts_cell = f"**{post_count}**"
        if feed_today_count > 0:
            posts_cell += f" (+{feed_today_count} new)"
        # Find latest post publication date
        latest_pub_dt = None
        for post in info.get('posts', []):
            dt = parse_datetime(post.get('pub_date'))
            if dt:
                if latest_pub_dt is None or dt > latest_pub_dt:
                    latest_pub_dt = dt
        last_update_str = latest_pub_dt.strftime('%Y-%m-%d %H:%M:%S') if latest_pub_dt else 'Never'

        lines.append(f"| {blog_link} | {posts_cell} | {status_str} | {last_update_str} |")
        
        if post_count > 0:
            blog_details.append((title, html_url, new_posts))
            
    lines.append("")
    lines.append(f"**Stats Summary:** Checked {len(cache)} feeds. **{total_new_posts}** total posts. Today: **{new_posts_today_count}** new posts.\n")
    
    # Detailed posts list
    if blog_details:
        lines.append("## Detailed Updates\n")
        for title, html_url, posts in blog_details:
            blog_link = f"[{title}]({html_url})" if html_url else title
            lines.append(f"### {blog_link} ({len(posts)} posts)")
            for post in posts:
                # Format local pub date
                p_dt = parse_datetime(post.get('pub_date'))
                date_str = p_dt.strftime('%b %d, %Y') if p_dt else 'Unknown Date'
                lines.append(f"* [{post['title']}]({post['link']}) - *{date_str}*")
            lines.append("")
    else:
        lines.append("No new posts published since the given date.")
        
    return "\n".join(lines)

def generate_html(cache, since_dt, last_run_dt=None):
    """Generates a premium, highly-styled HTML report."""
    total_new_posts = 0
    updated_blogs_count = 0
    
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    updated_today_count = 0
    new_posts_today_count = 0
    
    sorted_feeds = sorted(
        cache.items(),
        key=lambda x: (-get_latest_post_timestamp(x[1]), x[1]['title'].lower())
    )
    table_rows = []
    
    for xml_url, info in sorted_feeds:
        title = info['title']
        html_url = info['html_url']
        status_code = info['status']
        last_checked = info.get('last_checked', 'Never')
        error = info.get('error_msg')
        
        # Filter posts
        new_posts = []
        for post in info.get('posts', []):
            dt = parse_datetime(post.get('pub_date'))
            if dt and dt >= since_dt:
                new_posts.append(post)
                
        post_count = len(new_posts)
        total_new_posts += post_count
        if post_count > 0:
            updated_blogs_count += 1

        # Count today's updates
        has_post_today = False
        for post in info.get('posts', []):
            dt = parse_datetime(post.get('pub_date'))
            if dt and dt >= today_start:
                new_posts_today_count += 1
                has_post_today = True
        if has_post_today:
            updated_today_count += 1
            
        # Status styling classes
        if error:
            status_class = "status-error"
            status_text = f"Error ({status_code})"
            status_title = error
        elif status_code == 304:
            status_class = "status-cached"
            status_text = "304 Cached"
            status_title = "No changes detected on server"
        elif status_code == 200:
            status_class = "status-refreshed"
            status_text = "200 Refreshed"
            status_title = "Feed refreshed and parsed successfully"
        else:
            status_class = "status-unknown"
            status_text = f"HTTP {status_code}"
            status_title = ""

        # Find latest post publication date
        latest_pub_dt = None
        for post in info.get('posts', []):
            dt = parse_datetime(post.get('pub_date'))
            if dt:
                if latest_pub_dt is None or dt > latest_pub_dt:
                    latest_pub_dt = dt
        last_update_str = latest_pub_dt.strftime('%Y-%m-%d %H:%M:%S') if latest_pub_dt else 'Never'
        
        # Find today's posts for this specific feed
        today_posts = []
        for post in info.get('posts', []):
            dt = parse_datetime(post.get('pub_date'))
            if dt and dt >= today_start:
                today_posts.append(post)

        feed_today_count = len(today_posts)
        has_today = "true" if feed_today_count > 0 else "false"

        # Prepare Blog Column HTML
        if html_url:
            blog_link_html = f'<a href="{html_url}" target="_blank" class="blog-link toggle-posts" data-has-today="{has_today}">{title}</a>'
        else:
            blog_link_html = title

        if feed_today_count > 0:
            collapsible_items = []
            for p in today_posts:
                collapsible_items.append(f'<li><a href="{p["link"]}" target="_blank">{p["title"]}</a></li>')
            blog_link_html += f'<div class="today-posts-collapsible" style="display: none;"><ul class="today-posts-list">{"".join(collapsible_items)}</ul></div>'

        badge_class = "badge-update" if post_count > 0 else "badge-none"
        new_badge_html = f' <span class="badge badge-new">+{feed_today_count} new</span>' if feed_today_count > 0 else ""

        table_rows.append(f"""
        <tr data-posts-count="{post_count}" data-title="{title.lower()}">
            <td>{blog_link_html}</td>
            <td><span class="badge {badge_class}">{post_count} posts</span>{new_badge_html}</td>
            <td><span class="status-indicator {status_class}" title="{status_title}">{status_text}</span></td>
            <td class="text-muted">{last_update_str}</td>
        </tr>
        """)

    # Statistics Cards calculation
    total_feeds = len(cache)
    since_str = since_dt.strftime('%Y-%m-%d %H:%M:%S UTC')
    generated_str = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')

    # Load HTML Template from file
    template_path = os.path.join(os.path.dirname(__file__), "report_template.html")
    try:
        with open(template_path, "r", encoding="utf-8") as f:
            html_content = f.read()
    except Exception as e:
        print(f"Error loading HTML template '{template_path}': {e}. Returning error fallback.", file=sys.stderr)
        return f"<html><body><h1>Error loading template: {e}</h1></body></html>"

    # Do token replacements
    html_content = html_content.replace("{{since_str}}", since_str)
    html_content = html_content.replace("{{generated_str}}", generated_str)
    html_content = html_content.replace("{{total_feeds}}", str(total_feeds))
    html_content = html_content.replace("{{total_new_posts}}", str(total_new_posts))
    html_content = html_content.replace("{{new_posts_today_count}}", str(new_posts_today_count))
    html_content = html_content.replace("{{table_rows}}", "".join(table_rows))


    return html_content

def main():
    parser = argparse.ArgumentParser(description="Blog Feed Activity Tracker")
    parser.add_argument("--opml", default=DEFAULT_OPML, help="Path or URL to OPML file (default: blog-fetcher/tests.opml)")
    parser.add_argument("--since", help="Filter posts starting from date YYYY-MM-DD (default: 1st of current month)")
    parser.add_argument("--format", choices=["markdown", "html"], default="markdown", help="Output format (default: markdown)")
    parser.add_argument("--output", help="File to write report to (if not specified, no report is generated)")
    parser.add_argument("--cache", default=DEFAULT_CACHE, help="Path to cache file (default: blog-fetcher/feed_cache.json)")
    parser.add_argument("--exclude", help="Path to exclusion file containing URLs to ignore, one per line (default: blog-fetcher/exclude.yaml)")
    parser.add_argument("--concurrency", type=int, default=15, help="Number of concurrent workers (default: 15)")
    parser.add_argument("--ignore-ssl-errors", action="store_true", default=True, help="Ignore SSL validation errors (default: True)")

    args = parser.parse_args()

    # Determine since date
    if args.since:
        try:
            since_dt = datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            print(f"Error: Invalid date format for --since: '{args.since}'. Use YYYY-MM-DD.", file=sys.stderr)
            sys.exit(1)
    else:
        # Default to 1st of current month
        today = datetime.now(timezone.utc)
        since_dt = datetime(today.year, today.month, 1, tzinfo=timezone.utc)

    # Load OPML
    print(f"Loading OPML from: {args.opml}")
    ssl_context = ssl._create_unverified_context() if args.ignore_ssl_errors else None
    try:
        opml_bytes = load_opml(args.opml, ssl_context)
    except Exception as e:
        print(f"Error loading OPML: {e}", file=sys.stderr)
        sys.exit(1)

    # Parse OPML
    try:
        feeds = parse_opml(opml_bytes)
        print(f"Extracted {len(feeds)} feeds from OPML.")
    except Exception as e:
        print(f"Error parsing OPML: {e}", file=sys.stderr)
        sys.exit(1)

    # Load Exclusions
    exclude_urls = set()
    exclude_file = args.exclude
    if not exclude_file and os.path.exists(DEFAULT_EXCLUDE):
        exclude_file = DEFAULT_EXCLUDE

    if exclude_file and os.path.exists(exclude_file):
        try:
            with open(exclude_file, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
                if data and isinstance(data, dict):
                    exclude_list = data.get('exclude') or data.get('exclusions') or []
                    if isinstance(exclude_list, list):
                        for item in exclude_list:
                            if isinstance(item, str):
                                exclude_urls.add(item.strip())
            print(f"Loaded {len(exclude_urls)} exclusions from: {exclude_file}")
        except Exception as e:
            print(f"Warning: Failed to load exclusions from '{exclude_file}': {e}", file=sys.stderr)

    # Filter feeds based on exclusions
    filtered_feeds = [f for f in feeds if f['xml_url'] not in exclude_urls]
    excluded_count = len(feeds) - len(filtered_feeds)
    if excluded_count > 0:
        print(f"Excluded {excluded_count} feeds based on the exclusion configuration.")
    feeds = filtered_feeds

    if not feeds:
        print("No feeds found to crawl.", file=sys.stderr)
        sys.exit(0)

    # Load Cache
    cache = {}
    last_run_dt = None
    if os.path.exists(args.cache):
        try:
            with open(args.cache, "r", encoding="utf-8") as f:
                cache = json.load(f)
            print(f"Loaded existing cache database containing {len(cache)} entries.")
            
            # Extract last run date from existing cache entries
            checked_dates = []
            for entry in cache.values():
                if isinstance(entry, dict) and 'last_checked' in entry:
                    try:
                        checked_dates.append(datetime.fromisoformat(entry['last_checked']))
                    except Exception:
                        pass
            if checked_dates:
                last_run_dt = max(checked_dates)
        except Exception as e:
            print(f"Warning: Failed to load cache file, starting fresh. Reason: {e}", file=sys.stderr)

    # Log new feeds count
    new_feeds = [f for f in feeds if f['xml_url'] not in cache]
    print(f"Found {len(new_feeds)} new feeds in OPML since the last cache run.")

    # Log removed feeds count if any
    opml_urls = {f['xml_url'] for f in feeds}
    removed_feeds = [url for url in cache if url not in opml_urls]
    if removed_feeds:
        print(f"Note: {len(removed_feeds)} feeds have been removed from the OPML since the last cache run.")

    # Fetch feeds concurrently
    print(f"Fetching feeds (concurrency={args.concurrency})...")
    updated_cache = fetch_all_feeds(feeds, cache, concurrency=args.concurrency, ignore_ssl=args.ignore_ssl_errors)

    # Save Cache
    try:
        with open(args.cache, "w", encoding="utf-8") as f:
            json.dump(updated_cache, f, indent=2, ensure_ascii=False)
        print(f"Updated cache written back to: {args.cache}")
    except Exception as e:
        print(f"Error writing to cache file: {e}", file=sys.stderr)

    # Generate and Output Report (only if output file is specified)
    if args.output:
        if args.format == "markdown":
            report_content = generate_markdown(updated_cache, since_dt, last_run_dt)
        else:
            report_content = generate_html(updated_cache, since_dt, last_run_dt)

        try:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(report_content)
            print(f"Report successfully saved to: {args.output}")
        except Exception as e:
            print(f"Error writing report: {e}", file=sys.stderr)
            sys.exit(1)

if __name__ == "__main__":
    main()
