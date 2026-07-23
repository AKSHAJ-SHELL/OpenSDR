FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml README.md alembic.ini ./
COPY craftsman ./craftsman
COPY dashboard ./dashboard

RUN pip install --no-cache-dir -e ".[dashboard]"

CMD ["uvicorn", "craftsman.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
