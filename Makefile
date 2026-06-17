.PHONY: rust python run docker-build

rust:
	cd derivation-helper && cargo build --release
	cp derivation-helper/target/release/wwg-derive ./wwg-derive

python:
	python3.12 -m venv .venv
	. .venv/bin/activate && pip install -e .

run:
	. .venv/bin/activate && wwg run --config config.yaml

docker-build:
	docker compose build
