# Use the official lightweight Python image
FROM python:3.11-slim

# Install FFmpeg (this runs as root during the build phase, so it works perfectly)
RUN apt-get update && \
    apt-get install -y ffmpeg && \
    rm -rf /var/lib/apt/lists/*

# Set the working directory inside the container
WORKDIR /app

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application files
COPY . .

# Run the app using Gunicorn, binding to Render's default $PORT environment variable
CMD gunicorn app:app --bind 0.0.0.0:$PORT
