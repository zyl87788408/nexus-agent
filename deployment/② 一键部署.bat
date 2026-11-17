@echo off
chcp 936 >nul
setlocal EnableDelayedExpansion
title NEXUS 引擎 - 一键部署
color 0B

cd /d "%~dp0"

rem 程序被打包成单个 nexus.pyz；缺它说明压缩包没解压完整
if not exist "nexus.pyz" (
    echo ==============================================================
    echo   [错误] 缺少 nexus.pyz
    echo ==============================================================
    echo.
    echo   请确认：
    echo     1. 已把压缩包完整解压（不是直接在压缩包里双击运行）
    echo     2. nexus.pyz 与本脚本在同一个文件夹下
    echo.
    pause
    exit /b 1
)

set "PORT=8085"
set "MIRROR=https://pypi.tuna.tsinghua.edu.cn/simple"
set "HEALTH=http://127.0.0.1:%PORT%/health"

echo ==============================================================
echo                NEXUS 引擎 - 一键部署
echo ==============================================================
echo.

rem ================= 1. 检查 Python =================
echo [1/6] 检查 Python ...
python -c "import sys;sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>&1
if errorlevel 1 (
    echo.
    echo   [错误] 未找到 Python 3.10 或更高版本。
    echo.
    echo   请先安装 Python 3.10+ ，安装时务必勾选 "Add Python to PATH"，
    echo   安装完成后重新运行本脚本。下载地址：https://www.python.org/downloads/
    echo.
    pause
    exit /b 1
)
for /f "delims=" %%V in ('python -c "import sys;print(str(sys.version_info[0])+'.'+str(sys.version_info[1]))"') do set "PYVER=%%V"
echo   [OK] Python !PYVER!
echo.

rem ================= 2. 清理旧进程 =================
echo [2/6] 清理 %PORT% 端口旧进程 ...
set "OLDPID="
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /C:":%PORT% " ^| findstr /I "LISTENING"') do (
    if not "%%P"=="0" set "OLDPID=%%P"
)
if defined OLDPID (
    echo   发现旧进程 PID !OLDPID! ，正在结束 ...
    taskkill /F /PID !OLDPID! >nul 2>&1
    if errorlevel 1 (
        echo   [警告] 结束进程失败，可能需要以管理员身份运行本脚本
    ) else (
        echo   [OK] 已结束旧进程
    )
    timeout /t 2 /nobreak >nul
) else (
    echo   [OK] 端口空闲，无需清理
)
echo.

rem ================= 3. 安装依赖 =================
echo [3/6] 安装依赖（清华镜像源）...
python -m pip install --upgrade pip -i %MIRROR% >nul 2>&1
python -m pip install -r requirements.txt -i %MIRROR%
if errorlevel 1 (
    echo.
    echo   [错误] 依赖安装失败。
    echo.
    echo   请依次检查：
    echo     1. 网络是否可用，能否访问 %MIRROR%
    echo     2. 安全软件是否拦截了 pip ，尝试临时关闭或将本目录加入白名单
    echo     3. 重新运行本脚本重试
    echo.
    pause
    exit /b 1
)
echo   [OK] 依赖安装完成
echo.

rem ================= 4. 后台启动 =================
echo [4/6] 启动 NEXUS 服务 ...
start "NEXUS Server" /min python nexus.pyz
echo   正在启动，等待服务就绪 ...
timeout /t 10 /nobreak >nul
echo.

rem ================= 5. 健康检查 =================
echo [5/6] 健康检查 %HEALTH%
set "OK=0"
set /a TRY=0

:HEALTHCHECK
set /a TRY+=1
curl -s --max-time 5 "%HEALTH%" >"%TEMP%\nexus_health.txt" 2>nul
findstr /C:"ok" "%TEMP%\nexus_health.txt" >nul 2>&1
if not errorlevel 1 (
    set "OK=1"
    goto HEALTHDONE
)
if %TRY% LSS 5 (
    echo   第 %TRY% 次检测未通过，5 秒后重试 ...
    timeout /t 5 /nobreak >nul
    goto HEALTHCHECK
)
:HEALTHDONE
if exist "%TEMP%\nexus_health.txt" del /q "%TEMP%\nexus_health.txt" >nul 2>&1
echo.

if "!OK!"=="1" (
    color 0A
    echo ==============================================================
    echo.
    echo              部 署 成 功
    echo.
    echo           NEXUS 服务已启动并运行正常
    echo.
    echo           服务地址: http://127.0.0.1:%PORT%
    echo           健康检查: %HEALTH%
    echo.
    echo           请勿关闭已最小化的 "NEXUS Server" 窗口
    echo ==============================================================
    echo.
    rem ---- 模型安装引导（可选，可跳过）----
    call :MODELSETUP

    echo   运行日志：%~dp0data\logs\nexus.log
    echo   （出问题时把这个文件发给技术支持，或运行 ④ 常用工具 导出诊断包）
    echo.

    rem ---- 设置为开机自启，避免每次重启都要重新部署 ----
    if exist "③ 开机自启.bat" (
        call "③ 开机自启.bat" /quiet
    ) else (
        echo   [注意] 未找到 ③ 开机自启.bat ，跳过开机自启设置
    )

    timeout /t 40
) else (
    color 0C
    echo ==============================================================
    echo.
    echo              部 署 失 败
    echo.
    echo   健康检查未通过，服务可能未能正常启动。
    echo.
    echo   请把本窗口截图发给客服，并附上以下信息：
    echo     - 已最小化的 "NEXUS Server" 窗口中的报错内容
    echo     - 本窗口的完整输出
    echo.
    echo   常见原因：
    echo     1. 依赖未安装完整，请重新运行本脚本
    echo     2. %PORT% 端口被其他程序占用
    echo     3. 安全软件拦截了 python.exe
    echo ==============================================================
    echo.
    pause
)

goto :SCRIPT_END


rem ============================================================
rem  子程序：模型安装引导
rem  说明：NEXUS 本身不含模型，模型由 Ollama 持有。
rem        本段只做"问你一句 + 帮你执行 ollama pull"，
rem        不做任何静默下载。
rem ============================================================
:MODELSETUP
echo.
echo --------------------------------------------------------------
echo   [6/6] 安装模型（本地推理必需）
echo --------------------------------------------------------------
echo.

set "HASMODEL=0"
curl -s -f --max-time 4 http://127.0.0.1:11434/api/tags >"%TEMP%\nexus_ollama.txt" 2>nul
if not errorlevel 1 findstr /I /C:name "%TEMP%\nexus_ollama.txt" >nul 2>&1
if not errorlevel 1 set "HASMODEL=1"
if exist "%TEMP%\nexus_ollama.txt" del /q "%TEMP%\nexus_ollama.txt" >nul 2>&1
curl -s -f --max-time 4 http://127.0.0.1:8080/v1/models >nul 2>&1
if not errorlevel 1 set "HASMODEL=1"

if "!HASMODEL!"=="1" goto MODEL_READY

rem 下载依赖 ollama 命令行，必须先确认可用；
rem 它默认装在用户目录，常不在 PATH 中，这里补一次
where ollama >nul 2>&1
if errorlevel 1 (
    if exist "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" (
        set "PATH=!LOCALAPPDATA!\Programs\Ollama;!PATH!"
    )
)
where ollama >nul 2>&1
if errorlevel 1 goto MODEL_NO_OLLAMA

rem 已装但服务未起时，先拉起服务再进入下载
curl -s -f --max-time 3 http://127.0.0.1:11434/api/tags >nul 2>&1
if not errorlevel 1 goto MODEL_PICK
goto MODEL_CHECK_SERVICE

:MODEL_READY
echo   [OK] 已有可用模型，无需安装。
echo.
exit /b 0

:MODEL_NO_OLLAMA
echo   [注意] 未检测到 Ollama。
echo.
echo   NEXUS 只是一个调度层，自身不包含模型。
echo   必须先安装 Ollama（免费），才能下载和使用模型。
echo.
set "ANS="
set /p "ANS=   是否现在打开 Ollama 下载页面？[Y/N]: "
if /I "!ANS!"=="Y" start "" https://ollama.com/download
echo.
echo   安装完 Ollama 后，请重新运行本脚本，脚本会自动帮你下载模型。
echo   也可以手动执行：ollama pull qwen2.5:3b
echo.
exit /b 0

:MODEL_CHECK_SERVICE
curl -s -f --max-time 3 http://127.0.0.1:11434/api/tags >nul 2>&1
if not errorlevel 1 goto MODEL_PICK
echo   Ollama 已安装但未运行，正在后台启动 ...
start "Ollama Server" /min ollama serve
timeout /t 8 /nobreak >nul
curl -s -f --max-time 5 http://127.0.0.1:11434/api/tags >nul 2>&1
if errorlevel 1 (
    echo   [注意] Ollama 启动失败，请手动打开 Ollama 应用后再运行本脚本。
    echo.
    exit /b 0
)
echo   [OK] Ollama 已启动
echo.

:MODEL_PICK
set "MEMGB="
rem 写入临时文件再读，避免 for /f 命令串中的括号被 cmd 误解析
powershell -NoProfile -Command "[math]::Floor((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory/1GB)" >"%TEMP%\nexus_mem.txt" 2>nul
for /f "usebackq delims=" %%M in ("%TEMP%\nexus_mem.txt") do set "MEMGB=%%M"
if exist "%TEMP%\nexus_mem.txt" del /q "%TEMP%\nexus_mem.txt" >nul 2>&1
echo !MEMGB! | findstr /R "^[0-9][0-9]*$" >nul 2>&1
if errorlevel 1 set "MEMGB=8"

set "REC=qwen2.5:3b"
set "RECSIZE=约 2 GB"
if !MEMGB! GEQ 16 set "REC=qwen2.5:7b"
if !MEMGB! GEQ 16 set "RECSIZE=约 5 GB"
if !MEMGB! GEQ 32 set "REC=qwen2.5:14b"
if !MEMGB! GEQ 32 set "RECSIZE=约 9 GB"

echo   本机内存：!MEMGB! GB
echo   推荐模型：!REC!  ^(!RECSIZE!^)
echo.
echo   下载需要联网一次，之后即可完全离线运行。
echo.
set "ANS="
set /p "ANS=   是否现在下载该模型？[Y/N]: "
rem 只认 Y，其余（含空、含特殊字符）一律跳过，避免 ! 被解析
if /I "!ANS!"=="Y" goto MODEL_DO_PULL
goto MODEL_SKIP

:MODEL_DO_PULL

echo.
echo   正在下载 !REC! ，请耐心等待 ...
echo   下载过程请勿关闭本窗口。
echo.
ollama pull !REC!
if errorlevel 1 goto MODEL_PULL_FAIL

echo.
echo   [OK] 模型 !REC! 下载完成，正在验证 NEXUS 是否识别 ...
timeout /t 3 /nobreak >nul
curl -s --max-time 10 http://127.0.0.1:%PORT%/v1/models >"%TEMP%\nexus_m.txt" 2>nul
findstr /I /C:"!REC!" "%TEMP%\nexus_m.txt" >nul 2>&1
if errorlevel 1 (
    echo   [注意] 模型已下载，但 NEXUS 尚未识别，请等待 10 秒后再提问。
) else (
    echo   [OK] NEXUS 已识别到 !REC! ，现在可以正常提问了。
)
if exist "%TEMP%\nexus_m.txt" del /q "%TEMP%\nexus_m.txt" >nul 2>&1
echo.
exit /b 0

:MODEL_PULL_FAIL
echo.
echo   [错误] 模型下载失败。
echo.
echo   常见原因：
echo     1. 网络不稳定或无法访问 ollama.com ，请重试
echo     2. Ollama 服务异常，可先执行 ollama serve 再重试
echo     3. 磁盘空间不足，!REC! 约需 !RECSIZE!
echo.
echo   稍后可手动执行：ollama pull !REC!
echo.
exit /b 0

:MODEL_SKIP
echo.
echo   已跳过模型下载。
echo.
echo   稍后可随时手动安装，无需重装 NEXUS：
echo       ollama pull !REC!
echo.
echo   装好后直接提问即可，NEXUS 会自动识别，不用重启。
echo.
exit /b 0

:SCRIPT_END

endlocal
