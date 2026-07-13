@echo off
REM 卸载 nsfocus-monitor 服务
REM 用法 (管理员 cmd): uninstall-nssm-service.bat

setlocal
set "SVC_NAME=NSFocusMonitor"

where nssm >nul 2>&1
if errorlevel 1 (
    echo [WARN] nssm.exe not in PATH,跳过 (服务可能已通过其他方式移除)
    goto :check
)

:check
nssm status "%SVC_NAME%" >nul 2>&1
if errorlevel 1 (
    echo [INFO] 服务 "%SVC_NAME%" 未安装
    pause
    exit /b 0
)

echo 停止服务 %SVC_NAME%...
nssm stop "%SVC_NAME%"
timeout /t 3 /nobreak >nul

echo 移除服务 %SVC_NAME%...
nssm remove "%SVC_NAME%" confirm

echo.
echo [OK] 服务 "%SVC_NAME%" 已卸载
echo 数据目录保留不动 (如不再使用,手动删除 %LOCALAPPDATA%\nsfocus-monitor-data\)
pause
