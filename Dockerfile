FROM python:3.12-slim-bookworm

WORKDIR /app

# OR-Tools и прочие зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY TaxiLocations/ ./TaxiLocations/

RUN mkdir -p /app/data \
    && if [ -f ./TaxiLocations/employees.json ]; then \
         mv ./TaxiLocations/employees.json /app/data/employees.json; \
       else \
         echo '[]' > /app/data/employees.json; \
       fi \
    && ln -s /app/data/employees.json ./TaxiLocations/employees.json

WORKDIR /app/TaxiLocations
ENV PYTHONPATH=/app/TaxiLocations
ENV FLASK_PORT=4017

EXPOSE 4017

CMD ["python", "main.py"]
