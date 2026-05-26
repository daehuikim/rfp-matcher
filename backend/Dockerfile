FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
      libreoffice \
      poppler-utils \
      curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml ./
RUN pip install --no-cache-dir -e .

COPY app ./app

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
