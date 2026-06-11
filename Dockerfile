# Dockerfile for Synology MCP Server
FROM python:3.13-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt requirements-http.txt ./

# Install Python dependencies. The default image (stdio / Xiaozhi) installs only
# requirements.txt. The HTTP/SSE image (docker-compose.http.yml) passes
# INSTALL_HTTP=true to additionally install mcp-proxy from requirements-http.txt.
ARG INSTALL_HTTP=false
RUN pip install --no-cache-dir -r requirements.txt \
    && if [ "$INSTALL_HTTP" = "true" ]; then \
        pip install --no-cache-dir -r requirements-http.txt; \
    fi

# Copy source code
COPY src/ ./src/
COPY main.py .
COPY .env* ./

# Create logs directory
RUN mkdir -p logs

# Create non-root user for security
RUN useradd -m -u 1000 mcpuser && chown -R mcpuser:mcpuser /app
USER mcpuser

# Set environment variables for MCP
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

# Default command - supports both stdio (Claude/Cursor) and WebSocket (Xiaozhi) modes
# Mode is controlled by ENABLE_XIAOZHI environment variable
CMD ["python", "main.py"] 