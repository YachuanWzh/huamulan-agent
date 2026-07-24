FROM python:3.12-slim

WORKDIR /app

# Install uv package manager
RUN pip install --no-cache-dir uv

# Copy dependency manifest first (for layer caching across builds)
COPY pyproject.toml ./

# Copy application source (required before pip install so the package can build)
COPY src/ ./src/
COPY evaluation/ ./evaluation/
COPY knowledge/ ./knowledge/

# Install runtime dependencies + the package itself
RUN uv pip install --system "." \
    && pip uninstall -y uv

# Skills are bundled inside the source tree
ENV SKILLS_DIR=/app/src/personal_assistant/skills

EXPOSE 8000

CMD ["uvicorn", "personal_assistant.api.server:app", "--host", "0.0.0.0", "--port", "8000"]
