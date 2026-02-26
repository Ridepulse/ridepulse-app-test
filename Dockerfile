FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app/ ./app/

# ride-info is mounted as a volume so you can edit JSON without rebuilding
# data/ is also a volume (auto-generated live.json, calendar.json)

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
