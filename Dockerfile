FROM python:3.11-slim

WORKDIR /app

COPY app/requirements.txt ./app/requirements.txt
RUN pip install --no-cache-dir -r app/requirements.txt

COPY framework/ ./framework/
COPY app/ ./app/
COPY .streamlit/ ./.streamlit/

EXPOSE 8080

CMD ["streamlit", "run", "app/app.py", "--server.port=8080", "--server.address=0.0.0.0"]
