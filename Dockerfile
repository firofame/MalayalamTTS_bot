FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    wget gnupg ca-certificates \
    libasound2 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
    libgbm1 libgtk-3-0 libnspr4 libnss3 libxcomposite1 \
    libxdamage1 libxfixes3 libxrandr2 libpango-1.0-0 \
    libpangocairo-1.0-0 libx11-xcb1 fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN playwright install firefox

COPY . .

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
