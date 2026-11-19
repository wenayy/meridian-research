.PHONY: install test lint demo bench bench-llm serve docker

install:
	pip install -e ".[dev]"

test:
	pytest -q

lint:
	ruff check src tests scripts

demo:            ## full pipeline offline: scripted model + synthetic web, no keys
	meridian research "What is the state of the grid-scale sodium-ion battery market in 2025, and who are the leading companies?" --offline

bench:           ## benchmark with your real LLM on the offline synthetic web
	meridian bench

bench-offline:
	meridian bench --offline

serve:
	meridian serve

docker:
	docker build -t meridian . && docker run --env-file .env -p 8000:8000 meridian
