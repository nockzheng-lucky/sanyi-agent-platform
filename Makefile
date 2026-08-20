PY := .venv/bin/python
PIP := .venv/bin/pip

.PHONY: venv install dev token test run

venv:
	python3 -m venv .venv

install: venv
	$(PIP) install -e '.[dev]'

dev:
	LLM_MOCK=1 $(PY) -m uvicorn app.main:app --reload --port 8100

token:
	$(PY) scripts/create_token.py --name dev --quota 100000 --days 30

test:
	$(PY) -m pytest -q
