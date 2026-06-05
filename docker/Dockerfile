FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir fastapi uvicorn pydantic requests pytest httpx

COPY test_runner.py /app/test_runner.py

CMD ["python", "test_runner.py"]
