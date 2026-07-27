.PHONY: blaugust test install help

help:
	@echo "Available targets:"
	@echo "  install   - Install python dependencies"
	@echo "  blaugust  - Fetch feeds and generate HTML activity report starting 2026-07-01"

install:
	pip install -r requirements.txt

test:
	python3 blog_fetcher.py --opml tests.opml --format markdown --output report.md --cache test_cache.json

blaugust:
	python3 blog_fetcher.py --opml https://godless-internets.org/blaugust-2026.opml --since 2026-07-01 --format html --output report.html --cache blaugust2026_cache.json
