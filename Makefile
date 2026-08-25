.PHONY: backend-dev frontend-dev test backend-test frontend-test build

backend-dev:
	cd backend && ../.venv/bin/uvicorn app.main:app --reload --port 8000

frontend-dev:
	cd frontend && npm run dev

backend-test:
	cd backend && ../.venv/bin/pytest

frontend-test:
	cd frontend && npm test -- --run

test: backend-test frontend-test

build:
	cd frontend && npm run build

