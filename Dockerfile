FROM python:3.11-slim

# Install system dependencies.
# curl  — required by the Docker healthcheck (curl -f http://localhost:8000/health/)
# libpq-dev + gcc — required to compile psycopg2 from source
RUN apt-get update && apt-get install -y \
    curl \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies before copying source so this layer is cached
# unless requirements.txt changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the entire project source into the image.
COPY . .

# entrypoint.sh must be executable inside the container.
RUN chmod +x /app/entrypoint.sh

EXPOSE 8000