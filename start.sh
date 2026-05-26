#!/bin/bash
# 一键启动脚本：自动检测环境、初始化目录、配置环境变量、启动服务

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── 颜色 ─────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${GREEN}=== 绿盟升级监控平台 - 一键启动 ===${NC}"

# ── 1. Python 版本检查 ─────────────────────────────────────
PYTHON=$(command -v python3 || command -v python)
if [ -z "$PYTHON" ]; then
    echo -e "${RED}❌ 未找到 Python3，请先安装 Python 3.8+${NC}"
    exit 1
fi
echo -e "${GREEN}✓${NC} Python: $($PYTHON --version)"

# ── 2. 虚拟环境 ─────────────────────────────────────────────
if [ ! -d "venv" ]; then
    echo -e "${YELLOW}创建虚拟环境...${NC}"
    $PYTHON -m venv venv
fi
. venv/bin/activate

# ── 3. 依赖安装 ─────────────────────────────────────────────
echo -e "${YELLOW}安装依赖...${NC}"
pip install -q -r requirements.txt
echo -e "${GREEN}✓${NC} 依赖安装完成"

# ── 4. 环境变量配置（自动化） ──────────────────────────────
if [ ! -f ".env" ]; then
    echo -e "${YELLOW}首次部署，自动生成环境变量...${NC}"
    SECRET_KEY=$($PYTHON -c "import secrets; print(secrets.token_hex(32))")
    JWT_SECRET=$($PYTHON -c "import secrets; print(secrets.token_hex(32))")

    cat > .env << EOF
# 自动生成于 $(date '+%Y-%m-%d %H:%M:%S')
MONITOR_SECRET_KEY=$SECRET_KEY
MONITOR_JWT_SECRET=$JWT_SECRET
MONITOR_PORT=9999
MONITOR_DATA_DIR=$SCRIPT_DIR/data
MONITOR_LOG_DIR=$SCRIPT_DIR/logs
MONITOR_COLLECT_INTERVAL=4
MONITOR_ROLLBACK_CONFIRM=2
MONITOR_ATTACHMENT_MAX_SIZE=10485760
EOF
    echo -e "${GREEN}✓${NC} 环境变量已生成: .env"
else
    echo -e "${GREEN}✓${NC} 使用已有环境变量: .env"
fi

# ── 5. 数据目录初始化（自动化） ────────────────────────────
mkdir -p data logs
echo -e "${GREEN}✓${NC} 数据目录就绪: data/ logs/"

# ── 6. 检测并创建管理员账户（自动化） ──────────────────────
ADMIN_GENERATED=""
_check_admin() {
    # 调用注册接口，无用户时自动创建并返回密码
    RESPONSE=$(curl -s -X POST http://127.0.0.1:${MONITOR_PORT:-9999}/api/auth/register 2>/dev/null)
    if echo "$RESPONSE" | grep -q '"code":0'; then
        PASSWORD=$(echo "$RESPONSE" | $PYTHON -c "import sys,json; d=json.load(sys.stdin); print(d['data']['password'])" 2>/dev/null)
        if [ -n "$PASSWORD" ]; then
            ADMIN_GENERATED="$PASSWORD"
        fi
    fi
}

# 后台启动服务
echo -e "${YELLOW}启动服务中...${NC}"
nohup python3 run.py > logs/stdout.log 2>&1 &
APP_PID=$!
sleep 3

# 检测管理员账户状态
if [ -n "$APP_PID" ] && kill -0 $APP_PID 2>/dev/null; then
    _check_admin
    if [ -n "$ADMIN_GENERATED" ]; then
        echo -e "${GREEN}✓${NC} 管理员账户已自动创建（首次部署）"
    else
        echo -e "${GREEN}✓${NC} 使用已有管理员账户"
    fi
fi

echo -e "${YELLOW}按 Ctrl+C 停止服务${NC}"
echo -e ""
echo -e "${GREEN}✅ 启动完成！${NC}"
echo -e "   访问地址: http://127.0.0.1:${MONITOR_PORT:-9999}"
echo -e "   日志目录: logs/"
if [ -n "$ADMIN_GENERATED" ]; then
    echo -e ""
    echo -e "${YELLOW}⚠️  首次部署 - 管理员密码（仅显示一次，务必保存）:${NC}"
    echo -e "   用户名: admin"
    echo -e "   密码: ${ADMIN_GENERATED}"
fi
echo -e ""
echo -e "${YELLOW}按 Ctrl+C 停止服务${NC}"
wait $APP_PID 2>/dev/null || true