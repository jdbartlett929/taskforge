FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY pyproject.toml requirements.lock ./
COPY taskforge ./taskforge
RUN pip install -r requirements.lock && pip install --no-deps . \
    && useradd --uid 10001 --create-home appuser
USER appuser
EXPOSE 8000
CMD ["uvicorn", "taskforge.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]

