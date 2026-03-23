FROM python:3.12-slim-bookworm

WORKDIR /app

# OR-Tools и прочие зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY TaxiLocations/ ./TaxiLocations/

WORKDIR /app/TaxiLocations
ENV PYTHONPATH=/app/TaxiLocations
ENV FLASK_PORT=5002

EXPOSE 5002

CMD ["python", "main.py"]
