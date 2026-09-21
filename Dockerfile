FROM python:3.12-slim
WORKDIR /app
COPY telegram-ops/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY telegram-ops/app ./app
COPY telegram-ops/static ./static
COPY telegram-ops/templates ./templates
COPY telegram-ops/scripts ./scripts
RUN useradd --uid 10001 --create-home console && mkdir -p /app/data && chown -R console:console /app
USER console
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
