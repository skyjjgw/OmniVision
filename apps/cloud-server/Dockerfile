FROM python:3.10-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends gcc libgl1 libglib2.0-0 && rm -rf /var/lib/apt/lists/*
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000 6000
CMD ["uvicorn", "backend:app", "--host", "0.0.0.0", "--port", "8000"]
