FROM python:3.10-slim

LABEL maintainer="NSFocus Monitor"
LABEL description="绿盟升级监控平台"

# 环境变量默认值（可通过 -e 或 env_file 覆盖）
ENV MONITOR_PORT=9999 \
    MONITOR_SECRET_KEY=change-me-in-production \
    MONITOR_JWT_SECRET=change-me-in-production \
    MONITOR_DATA_DIR=/app/data \
    MONITOR_LOG_DIR=/app/logs \
    PYTHONUNBUFFERED=1

WORKDIR /app

# 先安装依赖（利用缓存）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用代码
COPY . .

# 初始化目录并安装
RUN mkdir -p data logs && \
    if [ ! -f data/nsfocus_monitor.db ]; then \
        python3 -c "from src.models import init_all_tables; init_all_tables()" || true; \
    fi

EXPOSE ${MONITOR_PORT}

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:${MONITOR_PORT}/api/health || exit 1

CMD ["python3", "-B", "run.py"]