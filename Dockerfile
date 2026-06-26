FROM python:3.10-slim

LABEL maintainer="NSFocus Monitor"
LABEL description="绿盟升级监控平台"

# 环境变量默认值（可通过 -e 或 env_file 覆盖）
# MONITOR_HOST 默认 0.0.0.0:容器内必须监听所有接口,否则 docker -p 映射的外部流量进不来
# (坑 3:之前默认 127.0.0.1 时,新用户首次部署都踩 Connection reset by peer)
ENV MONITOR_HOST=0.0.0.0 \
    MONITOR_PORT=9999 \
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

# HEALTHCHECK 不用 curl(镜像不装),改用 python stdlib 调 health 端点
# (坑 4:之前用 curl 一直 exit 127,容器显示 unhealthy 但应用层正常)
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD python3 -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:${MONITOR_PORT}/api/health',timeout=5).status==200 else 1)" || exit 1

CMD ["python3", "-B", "run.py"]