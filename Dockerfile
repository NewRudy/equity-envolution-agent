FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
ARG INSTALL_PADDLEOCR_API=0
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt \
    && if [ "$INSTALL_PADDLEOCR_API" = "1" ]; then pip install --no-cache-dir "paddleocr>=3.3,<4"; fi \
    && groupadd --system app \
    && useradd --system --gid app --home-dir /app app

COPY --chown=app:app app.py ./app.py
COPY --chown=app:app equity_agent ./equity_agent
COPY --chown=app:app sample_data ./sample_data
COPY --chown=app:app .streamlit ./.streamlit

USER app
EXPOSE 8501
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health', timeout=3)"

CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true", "--browser.gatherUsageStats=false"]
