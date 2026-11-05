PYTHON ?= python3
ACTIONLINT ?= actionlint

.PHONY: generate lint test
generate:
	$(PYTHON) scripts/render.py

lint:
	$(PYTHON) -m compileall -q scripts tests
	$(PYTHON) scripts/render.py --check
	$(ACTIONLINT) -shellcheck= -pyflakes= .github/workflows/*.yaml

test:
	$(PYTHON) -m unittest discover -s tests -v
