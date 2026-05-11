FROM python:3.11-slim

WORKDIR /app

# WHY: copy requirements.txt BEFORE the app source so this expensive
# pip install layer is cached as long as requirements.txt is unchanged

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

# WHY: 0.0.0.0 binds to all interfaces inside the container;
# the host-to-container port mapping is handled by docker-compose
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

