@echo off
REM =============================================================
REM 安装 nsfocus-monitor 为 Windows 服务
REM 工具: nssm (Non-Sucking Service Manager)
REM 下载: https://nssm.cc/download/
REM       解压得到 win64/nssm.exe,建议放 C:\Windows\System32 或 PATH 目录
REM =============================================================
REM 用法 (以管理员身份运行 cmd):
REM   cd /d C:\path\to\nsfocus-monitor
REM   install-nssm-service.bat

setlocal EnableDelayedExpansion

REM ── 可改配置 (环境变量) ───────────────────────────────────
REM 服务名(Windows 服务显示名)
set "SVC_NAME=NSFocusMonitor"

REM 服务显示描述
set "SVC_DESC=NSFOCUS Update Monitor - vendor portal rule diff + push notification service"

REM exe 路径(默认:脚本同目录的 nsfocus-monitor.exe)
set "EXE_PATH=%~dp0nsfocus-monitor.exe"

REM 工作目录(exe 运行时的 cwd,影响 %LOCALAPPDATA% 探测 → fallback 时落 exe 旁)
set "WORK_DIR=%~dp0"

REM 日志目录(stdout/stderr 输出)
set "LOG_DIR=%WORK_DIR%logs"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

REM 数据目录(显式指定避免"先 Program Files 后移 Tools 数据变孤儿"坑)
set "MONITOR_DATA_DIR=%LOCALAPPDATA%\nsfocus-monitor-data"

REM 监听端口(默认 9999)
set "MONITOR_PORT=9999"

REM JWT 密钥(生产必须改成 64 字符随机;首启动会自动生成并写入 data_dir/initial_password.txt)
if not defined MONITOR_JWT_SECRET set "MONITOR_JWT_SECRET=dev-jwt-secret-change-me"
set "MONITOR_SECRET_KEY=dev-secret-change-me"

REM ── 检查 nssm ──────────────────────────────────────────────
where nssm >nul 2>&1
if errorlevel 1 (
    echo [ERROR] nssm.exe not found in PATH
    echo 下载: https://nssm.cc/download/
    echo 解压 win64\nssm.exe 到 C:\Windows\System32\ 或加入 PATH
    pause
    exit /b 1
)

REM ── 检查 exe ──────────────────────────────────────────────
if not exist "%EXE_PATH%" (
    echo [ERROR] exe not found: %EXE_PATH%
    echo 确认 nsfocus-monitor.exe 在本目录
    pause
    exit /b 1
)

REM ── 检查是否已装 ─────────────────────────────────────────
nssm status "%SVC_NAME%" >nul 2>&1
if not errorlevel 1 (
    echo [WARN] 服务 "%SVC_NAME%" 已存在,卸载重装...
    nssm stop "%SVC_NAME%"
    timeout /t 3 /nobreak >nul
    nssm remove "%SVC_NAME%" confirm
)

echo ===========================================
echo Installing service: %SVC_NAME%
echo exe:    %EXE_PATH%
echo work:   %WORK_DIR%
echo data:   %MONITOR_DATA_DIR%
echo logs:   %LOG_DIR%\out.log, err.log
echo port:   %MONITOR_PORT%
echo ===========================================

REM ── 装服务 ────────────────────────────────────────────────
nssm install "%SVC_NAME%" "%EXE_PATH%"

REM 应用参数
nssm set "%SVC_NAME%" AppParameters ""
nssm set "%SVC_NAME%" AppDirectory "%WORK_DIR%"
nssm set "%SVC_NAME%" AppEnvironmentExtra "MONITOR_DATA_DIR=%MONITOR_DATA_DIR%^|MONITOR_PORT=%MONITOR_PORT%^|MONITOR_JWT_SECRET=%MONITOR_JWT_SECRET%^|MONITOR_SECRET_KEY=%MONITOR_SECRET_KEY%"
nssm set "%SVC_NAME%" AppStdout "%LOG_DIR%\out.log"
nssm set "%SVC_NAME%" AppStderr "%LOG_DIR%\err.log"
nssm set "%SVC_NAME%" AppRotateFiles 1
nssm set "%SVC_NAME%" AppRotateBytes 10485760

REM 服务元数据
nssm set "%SVC_NAME%" DisplayName "%SVC_NAME%"
nssm set "%SVC_NAME%" Description "%SVC_DESC%"

REM 启动方式:自动(系统启动时)
nssm set "%SVC_NAME%" Start SERVICE_AUTO_START

REM 失败时重启
nssm set "%SVC_NAME%" AppExit 0 Default
nssm set "%SVC_NAME%" AppRestartDelay 5000

REM 启动
nssm start "%SVC_NAME%"

echo.
echo ===========================================
echo 装成功! 服务名: %SVC_NAME%
echo.
echo 常用命令:
echo   nssm status %SVC_NAME%   - 看状态
echo   nssm start %SVC_NAME%    - 启动
echo   nssm stop %SVC_NAME%     - 停止
echo   nssm restart %SVC_NAME%  - 重启
echo   nssm remove %SVC_NAME% confirm  - 卸载
echo.
echo 验证服务:
echo   curl http://127.0.0.1:%MONITOR_PORT%/api/health
echo.
echo 数据目录: %MONITOR_DATA_DIR%
echo 日志:     %LOG_DIR%\out.log, err.log
echo ===========================================
pause
