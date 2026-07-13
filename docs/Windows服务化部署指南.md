# Windows 服务化部署(NSSM)

> 适用于 nsfocus-monitor.exe 单文件部署场景 — 把 exe 注册成 Windows 服务,开机自启、失败自动重启。

---

## 为什么用 NSSM

- **免费开源**(BSD 协议)
- **零代码修改**:exe 本身不需要支持 `--service` 参数
- **进程监控**:exe 崩溃自动重启(可配置延迟)
- **日志重定向**:stdout/stderr 自动落盘 + 轮转
- **服务权限控制**:可以以 LocalSystem / NetworkService / 普通用户身份运行

> 备选方案:pywin32 + `win32serviceutil`(需要改 exe 加 `nssm` 命令支持) → **不推荐**(改 exe 成本高于 NSSM 包装)

---

## 前置准备

### 1. 下载 nssm

- 官网: <https://nssm.cc/download/>
- 解压 `win64/nssm.exe` 到 `C:\Windows\System32\`(全局可用)或任意 PATH 目录

```cmd
:: 验证 nssm 可用
nssm
```

应看到 nssm 命令帮助。

### 2. 准备 nsfocus-monitor

- 已有 `nsfocus-monitor.exe` 单文件(从 GitHub Release 下载的最新版本)
- 首次运行 `nsfocus-monitor.exe` 双击 → 弹出账号密码窗口(初始 admin 密码写入 `data/initial_password.txt`)
- 确认 `http://localhost:9999/api/health` 返回 200 → exe 工作正常

---

## 装服务(以管理员身份运行 cmd)

### 一键安装(推荐)

把 `install-nssm-service.bat` 放到 exe **同目录**,以**管理员身份**运行:

```cmd
cd /d C:\path\to\nsfocus-monitor
install-nssm-service.bat
```

脚本会做:

1. 检测 nssm.exe 是否在 PATH
2. 检测 nsfocus-monitor.exe 是否在当前目录
3. 装服务(若已装会先卸载)
4. 配置工作目录、stdout/stderr 日志、env 变量
5. 设失败自动重启 + 启动服务
6. 打印常用命令清单

> **注意**:bat 内**自动设** `MONITOR_DATA_DIR=%LOCALAPPDATA%\nsfocus-monitor-data\`,避免「exe 旁 data/ 跟 LOCALAPPDATA 互不相认」坑(详见 §7.7)。

### bat 默认配置(可改)

```bat
set SVC_NAME=NSFocusMonitor              REM 服务名
set EXE_PATH=%~dp0nsfocus-monitor.exe    REM exe 路径(默认同目录)
set WORK_DIR=%~dp0                        REM 工作目录
set LOG_DIR=%~dp0logs                     REM 日志目录
set MONITOR_DATA_DIR=%LOCALAPPDATA%\nsfocus-monitor-data   REM 数据目录(关键!)
set MONITOR_PORT=9999                     REM 监听端口
set MONITOR_JWT_SECRET=dev-jwt-secret-change-me   REM ⚠️ 生产必改 64 字符随机
set MONITOR_SECRET_KEY=dev-secret-change-me      REM ⚠️ 生产必改 64 字符随机
```

生产环境**必须改**:
- `MONITOR_JWT_SECRET` / `MONITOR_SECRET_KEY` 各 64 字符随机(用 `python3 -c "import secrets; print(secrets.token_hex(32))"` 生成)
- 如果换 `MONITOR_DATA_DIR`,确保是**已存在且可写**的目录

---

## 验证服务

### 服务状态

```cmd
nssm status NSFocusMonitor
:: SERVICE_RUNNING (4) 表示正常运行
```

### 健康检查

```cmd
curl http://127.0.0.1:9999/api/health
:: 返回 {"status":"ok",...} 即健康
```

### 看日志

```cmd
type %~dp0logs\out.log
type %~dp0logs\err.log
```

日志在 `bat 工作目录\logs\`,**最大 10MB 轮转**(`AppRotateBytes=10485760`)。

---

## 日常运维

| 操作 | 命令 |
|---|---|
| 启动 | `nssm start NSFocusMonitor` |
| 停止 | `nssm stop NSFocusMonitor` |
| 重启 | `nssm restart NSFocusMonitor` |
| 状态 | `nssm status NSFocusMonitor` |
| 编辑配置 | `nssm edit NSFocusMonitor` (弹出 GUI) |
| 卸载服务 | `nssm remove NSFocusMonitor confirm` 或 `uninstall-nssm-service.bat` |

> **手工重启 vs 崩溃自动重启**:NSSM 配置 `AppExit=Default` + `AppRestartDelay=5000ms` → exe 退出后 5 秒自动拉起,适合「采集进程意外崩溃」场景。

---

## 升级 exe

> 升级 = 替换 exe,**不需要**重装服务。

1. `nssm stop NSFocusMonitor` — 停服务
2. 替换 `nsfocus-monitor.exe`(覆盖)
3. `nssm start NSFocusMonitor` — 启服务
4. 验证: `curl http://127.0.0.1:9999/api/health`

数据自动保留(`%LOCALAPPDATA%\nsfocus-monitor-data\` 跟 exe 位置无关,详见主升级文档 §7)。

---

## 卸载服务

```cmd
uninstall-nssm-service.bat
```

或手动:
```cmd
nssm stop NSFocusMonitor
nssm remove NSFocusMonitor confirm
```

> **数据目录不会删**(脚本和手动都不动) — 如不再使用,手动 `rmdir /s /q %LOCALAPPDATA%\nsfocus-monitor-data\`

---

## 已知坑 & 解决

| 坑 | 症状 | 解决 |
|---|---|---|
| `nssm` 不在 PATH | `install-nssm-service.bat` 报 `[ERROR] nssm.exe not found` | 把 nssm.exe 拷到 `C:\Windows\System32\` 或加 PATH |
| 端口 9999 占用 | 服务启动后立刻退出,err.log 报 `Address already in use` | 改 `MONITOR_PORT` env,或 `netstat -ano \| findstr 9999` 找占用进程 |
| 首次启动后 `initial_password.txt` 找不到 | admin 密码没记录 | 密码在**首次运行 exe 时**弹窗,bat 装的服务**也走同样流程** — 看 `logs/err.log` 有打印(有 GUI 看不到 stderr 的提醒) |
| 服务启动但页面打不开 | 检查 `logs/err.log` 看错 | 通常是 `flask_compress` 等依赖缺失 → 重新下载最新版 exe |
| 数据目录不一致 | 升级后数据"丢失" | 确认 `MONITOR_DATA_DIR` 跟老版本一致(默认 `%LOCALAPPDATA%\nsfocus-monitor-data\`) |
| 防火墙挡住 9999 | 局域网其他机器访问不到 | 控制面板 → Windows Defender 防火墙 → 添加入站规则 TCP 9999 |

---

## 配套脚本(项目仓库)

| 文件 | 作用 |
|---|---|
| `scripts/install-nssm-service.bat` | 一键装服务(配好所有参数) |
| `scripts/uninstall-nssm-service.bat` | 一键卸服务(保留数据) |

> **不需要**额外 bat 脚本(服务启动是 NSSM 管),但如果你想**手动**调试 exe(不走服务),直接双击 exe 或 `start nsfocus-monitor.exe` 即可。

---

## 跟 Docker 部署的对比

| 维度 | Windows exe + NSSM | Docker |
|---|---|---|
| 启动 | 系统启动自启 | 容器启动自启 |
| 数据目录 | `%LOCALAPPDATA%\nsfocus-monitor-data\` | `./data/`(卷挂载) |
| 日志 | `logs\out.log, err.log`(NSSM 轮转) | `docker logs` |
| 升级 | 停服务 → 换 exe → 启 | `docker pull && up -d` |
| 适合 | Windows 单机 / 小规模 | Linux 服务器 / 大规模 / K8s |

两者**互不影响** — 数据目录、env 变量、port 都独立。混用(开发用 exe、生产用 Docker)没问题。
