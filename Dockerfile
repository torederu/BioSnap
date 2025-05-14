# Base image with Python
FROM python:3.10-slim

# Install system dependencies (includes Chrome)
RUN apt-get update && apt-get install -y \
    wget unzip curl gnupg \
    chromium-driver chromium \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Set environment variables so Selenium finds Chrome
ENV CHROME_BIN=/usr/bin/chromium
ENV PATH=$PATH:/usr/bin/chromium

# Set work directory
WORKDIR /app

# Copy files
COPY . .

# Install Python dependencies
RUN pip install --upgrade pip
RUN pip install -r requirements.txt

# Default command
CMD ["streamlit", "run", "streamlit_app.py", "--server.port=10000", "--server.address=0.0.0.0"]

