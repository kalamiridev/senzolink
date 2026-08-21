FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY fusionsolar_mqtt.py .
COPY fusionsolar_api ./fusionsolar_api

RUN useradd --system --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app

USER appuser

CMD ["python", "-u", "fusionsolar_mqtt.py"]
