FROM prefecthq/prefect:3-python3.12

RUN pip install --no-cache-dir httpx

ENV PREFECT_API_URL=http://127.0.0.1:4200/api \
    PREFECT_SERVER_ANALYTICS_ENABLED=false \
    PREFECT_API_DATABASE_TIMEOUT=30 \
    PREFECT_UI_API_URL=http://localhost:4200/api \
    PREFECT_HOME=/data

WORKDIR /app
COPY scheduler.py gate.py book.py mcp_server.py entrypoint.sh ./
COPY skill/SKILL.md ./skill/
RUN chmod +x entrypoint.sh

EXPOSE 4200 8080
VOLUME /data
ENTRYPOINT ["./entrypoint.sh"]
