FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir '.[oci]' && useradd --create-home app && mkdir -p /app/runtime && chown app:app /app/runtime
USER app
EXPOSE 8787
CMD ["research-backend", "serve", "--host", "0.0.0.0", "--port", "8787"]
