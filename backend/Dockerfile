FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

WORKDIR /app

# PDF rendering uses PyMuPDF wheels; no external OCR programs are needed.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Fresh GitHub checkouts contain statute sources, not the generated vector index.
# Build once into the image, including the cached embedding model for queries.
RUN python -m scripts.build_research_index

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
