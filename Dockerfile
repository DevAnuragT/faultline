FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8501
# $PORT is set by Render / Fly / Cloud Run; falls back to 8501 locally.
CMD streamlit run streamlit_app.py --server.port=${PORT:-8501} --server.address=0.0.0.0 --server.headless=true
