FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8600
# $PORT is set by Render / Fly / Cloud Run. Set FAULTLINE_PUBLIC=true and the FALKORDB_* / GRAPH_NAME / GEMINI_API_KEY variables.
CMD uvicorn faultline.api:app --host 0.0.0.0 --port ${PORT:-8600}
