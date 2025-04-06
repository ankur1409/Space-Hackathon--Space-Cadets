FROM python:3.9-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .
# Create JSON files with proper structure
RUN echo '{"logs": []}' > logs.json && \
    echo '{"placements": []}' > placement.json && \
    echo '{"items": []}' > items.json && \
    echo '{"containers": []}' > containers.json
EXPOSE 8000
CMD ["python", "app.py", "--host", "0.0.0.0", "--port", "8000"]