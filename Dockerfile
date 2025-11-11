# Dockerfile (place in repo root)
FROM python:3.10-slim

ENV DEBIAN_FRONTEND=noninteractive
WORKDIR /app

# Install system deps if you need (uncomment if required)
# RUN apt-get update && apt-get install -y build-essential libpq-dev

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8080

# Streamlit server settings:
CMD ["streamlit", "run", "app.py", "--server.port=8080", "--server.address=0.0.0.0"]