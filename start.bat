@echo off
chcp 65001 >nul
setlocal

REM ============================================================
REM  CookAgent 一键启动脚本
REM  后端: FastAPI  (uvicorn, 端口 8000)
REM  前端: Vue 3    (vite,   端口 5173)
REM  依赖服务 (Postgres/Milvus/Redis/MinIO) 需 Docker，请另行启动:
REM      docker compose up -d
REM ============================================================

REM 强制 Python 用 UTF-8。本机 locale 若是 cp936(GBK)，pip 会用 GBK 去读
REM UTF-8 的 requirements.txt（含中文注释），直接抛 UnicodeDecodeError，
REM 导致依赖永远装不上。
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

set "ROOT_DIR=%~dp0"
if "%ROOT_DIR:~-1%"=="\" set "ROOT_DIR=%ROOT_DIR:~0,-1%"
cd /d "%ROOT_DIR%"

set "VENV_DIR=%ROOT_DIR%\.venv"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"

echo ==============================================
echo   CookAgent 一键启动
echo ==============================================

REM ---------- 0. 检查 .env ----------
if not exist ".env" (
    echo [SETUP] 未找到 .env，从模板复制...
    copy /y ".env.example" ".env" >nul
    echo [WARN] 已生成 .env，请填入 DASHSCOPE_API_KEY 等真实密钥后重新运行本脚本
    pause
    exit /b 1
)

REM ---------- 1. 后端环境 ----------
echo.
echo [1/3] 准备后端环境...

REM 只判断 python.exe 是否存在是不够的：虚拟环境创建中途被中断会残留
REM python.exe，却没有 pip / activate.bat。必须真的能 import pip 才算可用。
set "VENV_USABLE="
if exist "%VENV_PY%" (
    "%VENV_PY%" -c "import pip" >nul 2>&1
    if not errorlevel 1 set "VENV_USABLE=1"
)

if not defined VENV_USABLE (
    if exist "%VENV_DIR%" (
        echo [*] 检测到 .venv 不完整，正在重建...
        rmdir /s /q "%VENV_DIR%"
    ) else (
        echo [*] 首次运行，创建虚拟环境 .venv...
    )

    REM 优先用 py 启动器挑选解释器，避免误用 PATH 上第三方工具自带的 python
    set "BASEPY="
    py -3.13 -c "import sys" >nul 2>&1
    if not errorlevel 1 set "BASEPY=py -3.13"
    if defined BASEPY goto :have_basepy
    py -3 -c "import sys" >nul 2>&1
    if not errorlevel 1 set "BASEPY=py -3"
    if defined BASEPY goto :have_basepy
    python -c "import sys" >nul 2>&1
    if not errorlevel 1 set "BASEPY=python"
    :have_basepy
    if not defined BASEPY (
        echo [ERROR] 未找到 Python 3.11+ 解释器，请先安装 Python，并确保 py 或 python 可用
        pause
        exit /b 1
    )

    %BASEPY% -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo [ERROR] 创建虚拟环境失败（使用的解释器: %BASEPY%）
        pause
        exit /b 1
    )
    echo [*] 虚拟环境已创建（解释器: %BASEPY%）
)

REM 依赖检查用 .venv 内的 python，而不是 PATH 上的 python，
REM 否则环境不对也会"检查通过"
"%VENV_PY%" -c "import fastapi, uvicorn, langchain_openai, pymilvus" >nul 2>&1
if errorlevel 1 (
    echo [*] 安装后端依赖 requirements.txt（首次运行需要几分钟）...
    "%VENV_PY%" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo [ERROR] 后端依赖安装失败，上方是 pip 的原始报错，请据此排查
        pause
        exit /b 1
    )
)

REM 预检：确认应用能导入（配置、语法、依赖是否齐全），避免服务窗口一闪而过
"%VENV_PY%" -c "import app.main" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] 应用导入失败，原始报错如下：
    echo.
    "%VENV_PY%" -c "import app.main"
    echo.
    pause
    exit /b 1
)
echo [OK] 后端环境就绪

REM ---------- 2. 前端环境 ----------
echo.
echo [2/3] 准备前端环境...
where npm >nul 2>&1
if errorlevel 1 (
    echo [ERROR] 未找到 npm，请先安装 Node.js 18+
    pause
    exit /b 1
)
if not exist "frontend\node_modules" (
    echo [*] 首次运行，安装前端依赖 npm install...
    pushd "frontend"
    call npm install
    REM 必须先存下 errorlevel：popd 成功会把 errorlevel 重置为 0
    set "NPM_ERR=%errorlevel%"
    popd
    if not "%NPM_ERR%"=="0" (
        echo [ERROR] 前端依赖安装失败，上方是 npm 的原始报错
        pause
        exit /b 1
    )
)
echo [OK] 前端环境就绪

REM ---------- 3. 启动服务 ----------
echo.
echo [3/3] 启动服务...

REM 直接用 .venv 里的 python 启动，不经过 activate.bat —— activate.bat 一旦缺失，
REM "activate.bat && ... && uvicorn" 这条 && 链会在这里断开，uvicorn 根本不会执行
start "CookAgent-Backend" /D "%ROOT_DIR%" cmd /k .venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
start "CookAgent-Frontend" /D "%ROOT_DIR%\frontend" cmd /k npm run dev

echo.
echo ==============================================
echo   服务已启动（各自独立窗口运行，关闭窗口即停止）
echo.
echo     后端 API:   http://localhost:8000
echo     前端页面:   http://localhost:5173
echo     API 文档:   http://localhost:8000/docs
echo.
echo   提示：
echo   - 后端首次启动要连接 Milvus 并加载向量模型，请等日志出现
echo     "Application startup complete" 后再访问页面
echo   - Docker 依赖服务: docker compose up -d
echo   - 首次使用需导入菜谱数据: 先停掉后端，再运行
echo       .venv\Scripts\python.exe scripts\ingest_data.py
echo ==============================================
echo.
pause
endlocal
