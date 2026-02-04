# Dockerfile for notebooklm-py
# Builds a container that can be bound to a Cloudflare Worker
#
# Build:
#   docker build -t notebooklm-py .
#
# Run:
#   docker run -it notebooklm-py notebooklm --help
#
# With server:
#   docker run -p 8000:8000 notebooklm-py uvicorn notebooklm.server:app --host 0.0.0.0 --port 8000

FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Install system dependencies required for Playwright and general operation
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Required for Playwright Chromium
    libnss3 \
    libnspr4 \
    libdbus-1-3 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    libpango-1.0-0 \
    libcairo2 \
    # General utilities
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy package files
COPY pyproject.toml uv.lock ./
COPY src/ ./src/
COPY README.md ./

# Install uv for fast dependency management
RUN pip install --no-cache-dir uv

# Install the package with browser and server support
RUN uv pip install --system -e ".[browser,server]"

# Install Playwright browsers (Chromium only for minimal size)
RUN playwright install chromium

# Create a non-root user for security
RUN useradd --create-home --shell /bin/bash notebooklm
USER notebooklm

# Set home directory for credential storage
ENV HOME=/home/notebooklm

# Expose port for server mode
EXPOSE 8000

# Default command shows help
ENTRYPOINT ["notebooklm"]
CMD ["--help"]
