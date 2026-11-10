FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app/ /app/
# La base de datos queda en el volumen /data (ver docker-compose.yml)
ENV DB_PATH=/data/prov.db
EXPOSE 8066
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8066", "--proxy-headers"]
