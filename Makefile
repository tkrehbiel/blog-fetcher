.PHONY: blaugust build test install help

help:
	@echo "Available targets:"
	@echo "  install   - Install python dependencies"
	@echo "  blaugust  - Fetch feeds and generate HTML activity report starting 2026-08-01"

install:
	pip install -r requirements.txt

test:
	python3 blog_fetcher.py --opml tests.opml --format html --output report.html --cache test_cache.json

local:
	python3 blog_fetcher.py --opml https://godless-internets.org/blaugust-2026.opml --since 2026-08-01 --format html --output report.html --cache blaugustlocal2026_cache.json

build:
	mkdir -p public
	python3 blog_fetcher.py --opml https://godless-internets.org/blaugust-2026.opml --since 2026-08-01 --format html --output public/index.html --cache blaugust2026_cache.json
