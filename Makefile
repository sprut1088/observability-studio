.PHONY: install test demo assess check clean lint

install:
	pip install -e .
	pip install pytest

test:
	pytest tests/ -v

demo:
	python examples/demo_offline.py

assess:
	observascore assess --config config/config.yaml --output ./reports

check:
	observascore check --config config/config.yaml

list-rules:
	observascore list-rules

clean:
	rm -rf reports/ build/ dist/ *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
