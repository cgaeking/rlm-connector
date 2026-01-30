FROM python:3.11-slim

WORKDIR /app

# Install system dependencies for document parsing
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-deu \
    poppler-utils \
    libmagic1 \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir .

# Copy application code
COPY src/ ./src/
COPY config.example.yaml ./config.example.yaml

# Create data directory
RUN mkdir -p /app/data

# Expose ports
EXPOSE 8000 7860

# Default command: run both API and UI
CMD ["python", "-m", "src.main"]
