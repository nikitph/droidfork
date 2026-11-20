.PHONY: prepare demo serve benchmark verify upstream

prepare:
	uv run --python 3.12 python -m scripts.prepare

demo:
	uv run --python 3.12 python -m scripts.demo

serve:
	uv run --python 3.12 python -m droidfork.service

benchmark:
	uv run --python 3.12 python -m scripts.benchmark --modes fork,warm,cold

verify:
	uv run --python 3.12 python -m scripts.probe
	uv run --python 3.12 python -m scripts.app_benchmark

upstream:
	./scripts/install_androidworld.sh
	uv run --python 3.12 python -m scripts.upstream
