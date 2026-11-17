@echo off
chcp 936 >nul
setlocal EnableDelayedExpansion
title NEXUS 引擎 - 常用工具
color 0B

cd /d "%~dp0"
set "PORT=8085"

:MENU
cls
echo ==============================================================
echo                    NEXUS 引擎 - 常用工具
echo ==============================================================
echo.

rem 实时状态
set "RUNNING=否"
curl -s --max-time 3 "http://127.0.0.1:%PORT%/health" 2>nul | findstr /C:"ok" >nul 2>&1
if not errorlevel 1 set "RUNNING=是"

set "AUTOSTART=未设置"
if exist "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\NEXUS自启.vbs" set "AUTOSTART=已开启"

set "MODELN=0"
if exist "%TEMP%\nexus_mlist.txt" del /q "%TEMP%\nexus_mlist.txt" >nul 2>&1
curl -s --max-time 3 "http://127.0.0.1:%PORT%/v1/models" >"%TEMP%\nexus_mlist.txt" 2>nul
if exist "%TEMP%\nexus_mlist.txt" for %%L in ("%TEMP%\nexus_mlist.txt") do set "MODELN=%%~zL"
if exist "%TEMP%\nexus_mlist.txt" del /q "%TEMP%\nexus_mlist.txt" >nul 2>&1

echo   当前状态
echo   ----------------------------------------------------------
echo     服务运行：!RUNNING!
echo     开机自启：!AUTOSTART!
echo     地址：http://127.0.0.1:%PORT%
echo   ----------------------------------------------------------
echo.
echo   请选择操作（输入数字后回车）
echo.
echo     [1] 启动服务
echo     [2] 停止服务
echo     [3] 重启服务
echo     [4] 查看运行状态（详细）
echo     [5] 查看日志（最近 40 行）
echo     [6] 打开接口文档（/docs）
echo     [7] 查看已安装模型
echo     [8] 下载模型
echo     [9] 导出诊断包（发给技术支持）
echo    [10] 设置/取消开机自启
echo    [11] 修改端口
echo    [12] 一键备份配置（data 目录）
echo    [13] 切换模型来源（本地/自动/API 悬浮窗）
echo     [0] 退出
echo.

set "CH="
set /p "CH=   请输入编号: "
if "!CH!"=="1" goto DO_START
if "!CH!"=="2" goto DO_STOP
if "!CH!"=="3" goto DO_RESTART
if "!CH!"=="4" goto DO_STATUS
if "!CH!"=="5" goto DO_LOG
if "!CH!"=="6" goto DO_WEB
if "!CH!"=="7" goto DO_MODELS
if "!CH!"=="8" goto DO_PULL
if "!CH!"=="9" goto DO_DIAG
if "!CH!"=="10" goto DO_AUTO
if "!CH!"=="11" goto DO_PORT
if "!CH!"=="12" goto DO_BACKUP
if "!CH!"=="13" goto DO_SWITCH
if "!CH!"=="0" exit /b 0
goto MENU


:DO_SWITCH
echo.
call :FIND_PY
if not defined PYEXE (
    echo   [错误] 未找到可用的 Python ，请先运行 ② 一键部署.bat。
    goto HOLD
)
if not exist "悬浮窗.py" (
    echo   [错误] 缺少 悬浮窗.py ，请确认本目录完整。
    goto HOLD
)
echo   正在打开模型切换悬浮窗…
start "" "!PYEXE!" "%~dp0悬浮窗.py"
echo   [OK] 已打开。若没看到，请检查任务栏或屏幕右侧。
echo        窗口可拖动，始终置顶，点右上角 X 关闭。
goto HOLD


rem =========================================================
:DO_START
echo.
call :FIND_PY
if not defined PYEXE (
    echo   [错误] 未找到可用的 Python
    goto HOLD
)
call :IS_RUNNING
if "!UP!"=="1" (
    echo   [提示] 服务已经在运行，无需启动。
    goto HOLD
)
echo   正在启动 ...
start "NEXUS Server" /min "!PYEXE!" "%~dp0nexus.pyz"
call :WAIT_UP
if "!UP!"=="1" (
    echo   [OK] 启动成功 -> http://127.0.0.1:%PORT%
) else (
    echo   [错误] 启动失败，请查看日志（菜单第 5 项）
)
goto HOLD

rem =========================================================
:DO_STOP
echo.
call :IS_RUNNING
if "!UP!"=="0" (
    echo   [提示] 服务未在运行。
    goto HOLD
)
echo   正在停止占用 %PORT% 端口的进程 ...
set "STOPPED=0"
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /C:":%PORT% " ^| findstr /I "LISTENING"') do (
    taskkill /F /PID %%P >nul 2>&1
    if not errorlevel 1 set "STOPPED=1"
)
timeout /t 2 /nobreak >nul
call :IS_RUNNING
if "!UP!"=="0" (
    echo   [OK] 已停止服务。
) else (
    echo   [错误] 停止失败，可能需要管理员权限。
)
goto HOLD

rem =========================================================
:DO_RESTART
echo.
call :DO_STOP_SILENT
start "NEXUS Server" /min "!PYEXE!" "%~dp0nexus.pyz"
call :WAIT_UP
if "!UP!"=="1" (
    echo   [OK] 重启成功。
) else (
    echo   [错误] 重启失败，请查看日志。
)
goto HOLD

:DO_STOP_SILENT
call :FIND_PY
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /C:":%PORT% " ^| findstr /I "LISTENING"') do taskkill /F /PID %%P >nul 2>&1
timeout /t 2 /nobreak >nul
exit /b 0

rem =========================================================
:DO_STATUS
echo.
echo   ---------------- 详细状态 ----------------
call :FIND_PY
if defined PYEXE (echo   Python:      !PYEXE!) else (echo   Python:      未找到)
call :IS_RUNNING
if "!UP!"=="1" (echo   服务:        运行中) else (echo   服务:        未运行)
echo   端口 %PORT%:
netstat -ano | findstr /C:":%PORT% " | findstr /I "LISTENING" >nul 2>&1
if errorlevel 1 (echo     未监听) else (
    for /f "tokens=5" %%P in ('netstat -ano ^| findstr /C:":%PORT% " ^| findstr /I "LISTENING"') do echo     被 PID %%P 占用
)
if exist "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\NEXUS自启.vbs" (
    echo   开机自启:    已开启
) else (
    echo   开机自启:    未设置
)
echo.
echo   Ollama:
curl -s -f --max-time 3 http://127.0.0.1:11434/api/tags >nul 2>&1
if errorlevel 1 (echo     未运行) else (echo     运行中)
echo.
echo   data 目录：
if exist "%~dp0data" (
    for /f "usebackq delims=" %%S in (`powershell -NoProfile -Command "'{0:N1} MB' -f ((Get-ChildItem -Recurse -Force '%~dp0data' | Measure-Object Length -Sum).Sum/1MB)" 2^>nul`) do echo     占用 %%S
) else (
    echo     尚未创建
)
goto HOLD

rem =========================================================
:DO_LOG
echo.
if not exist "%~dp0data\logs\nexus.log" (
    echo   [提示] 还没有日志文件。
    echo          服务启动一次后才会生成。
    goto HOLD
)
echo   ---------------- 最近 40 行日志 ----------------
powershell -NoProfile -Command "Get-Content '%~dp0data\logs\nexus.log' -Tail 40" 2>nul
echo   ----------------------------------------------
echo.
echo   完整日志：%~dp0data\logs\nexus.log
goto HOLD

rem =========================================================
:DO_WEB
echo.
rem 根路径没有页面（404），/docs 是自带的接口调试页
echo   正在打开 http://127.0.0.1:%PORT%/docs
start "" "http://127.0.0.1:%PORT%/docs"
goto HOLD

rem =========================================================
:DO_MODELS
echo.
echo   正在读取模型列表 ...
curl -s --max-time 5 "http://127.0.0.1:%PORT%/v1/models" >"%TEMP%\nexus_m.txt" 2>nul
if errorlevel 1 (
    echo   [错误] 无法连接服务，请先启动。
    goto HOLD
)
findstr /C:"qwen" /C:"llama" /C:"gemma" /C:"phi" "%TEMP%\nexus_m.txt" >nul 2>&1
if errorlevel 1 (
    echo   [注意] 没有真实模型，只有兜底服务。
    echo          请用菜单第 8 项下载模型。
) else (
    echo   可用模型：
    powershell -NoProfile -Command "(Get-Content '%TEMP%\nexus_m.txt' -Raw | ConvertFrom-Json).data | ForEach-Object { '     ' + $_.id }" 2>nul
)
if exist "%TEMP%\nexus_m.txt" del /q "%TEMP%\nexus_m.txt" >nul 2>&1
echo.
echo   Ollama 本地模型：
where ollama >nul 2>&1
if errorlevel 1 (
    if exist "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" set "PATH=!LOCALAPPDATA!\Programs\Ollama;!PATH!"
)
ollama list 2>nul
if errorlevel 1 echo     （未安装或未运行 Ollama）
goto HOLD

rem =========================================================
:DO_PULL
echo.
where ollama >nul 2>&1
if errorlevel 1 (
    if exist "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" set "PATH=!LOCALAPPDATA!\Programs\Ollama;!PATH!"
)
where ollama >nul 2>&1
if errorlevel 1 (
    echo   [错误] 未检测到 Ollama。
    echo          请先安装 Ollama：https://ollama.com/download
    goto HOLD
)
set "MEMGB="
for /f "usebackq delims=" %%M in (`powershell -NoProfile -Command "[math]::Floor((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory/1GB)" 2^>nul`) do set "MEMGB=%%M"
if not defined MEMGB set "MEMGB=8"

set "REC=qwen2.5:3b"
if !MEMGB! GEQ 16 set "REC=qwen2.5:7b"
if !MEMGB! GEQ 32 set "REC=qwen2.5:14b"

echo   本机内存 !MEMGB! GB ，推荐：!REC!
echo.
echo   已装模型：
ollama list 2>nul
echo.
echo   常用可选：
echo     1. qwen2.5:3b    （2 GB，8G 内存）
echo     2. qwen2.5:7b    （5 GB，16G 内存）
echo     3. qwen2.5:14b   （9 GB，32G 内存）
echo     4. 手动输入模型名
echo.
set "MC="
set /p "MC=   选择（1-4，直接回车=下载推荐）: "
if "!MC!"=="1" set "PULLM=qwen2.5:3b"
if "!MC!"=="2" set "PULLM=qwen2.5:7b"
if "!MC!"=="3" set "PULLM=qwen2.5:14b"
if "!MC!"=="4" (
    set "PULLM="
    set /p "PULLM=   输入模型名: "
)
if not defined PULLM set "PULLM=!REC!"
echo.
echo   开始下载 !PULLM! （大文件，请耐心等待）...
echo.
ollama pull !PULLM!
if errorlevel 1 (
    echo.
    echo   [错误] 下载失败，请检查网络后重试。
) else (
    echo.
    echo   [OK] 下载完成。
    echo        服务会自动识别，无需重启。
)
goto HOLD

rem =========================================================
:DO_DIAG
echo.
echo   正在收集诊断信息 ...
set "DT=%TEMP%\nexus_diag_%RANDOM%"
mkdir "!DT!" 2>nul

call :FIND_PY
> "!DT!\info.txt" echo === NEXUS 诊断报告 ===
>>"!DT!\info.txt" echo 时间: %DATE% %TIME%
>>"!DT!\info.txt" echo.
>>"!DT!\info.txt" echo --- 系统 ---
powershell -NoProfile -Command "(Get-CimInstance Win32_OperatingSystem) | ForEach-Object { 'OS: ' + $_.Caption + ' Build ' + $_.BuildNumber + ' ' + $_.OSArchitecture }" >>"!DT!\info.txt" 2>nul
powershell -NoProfile -Command "'内存: ' + [math]::Floor((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory/1GB) + ' GB'" >>"!DT!\info.txt" 2>nul
>>"!DT!\info.txt" echo.
>>"!DT!\info.txt" echo --- Python ---
if defined PYEXE (
    >>"!DT!\info.txt" echo 路径: !PYEXE!
    "!PYEXE!" -c "import sys;print('版本:',sys.version)" >>"!DT!\info.txt" 2>&1
    "!PYEXE!" -c "import struct;print('位数:',struct.calcsize('P')*8,'位')" >>"!DT!\info.txt" 2>&1
    "!PYEXE!" -m pip --version >>"!DT!\info.txt" 2>&1
    "!PYEXE!" -c "import fastapi,uvicorn,pydantic;print('依赖: 已安装')" >>"!DT!\info.txt" 2>&1
) else (
    >>"!DT!\info.txt" echo 未找到 Python
)
>>"!DT!\info.txt" echo.
>>"!DT!\info.txt" echo --- 服务状态 ---
curl -s --max-time 3 "http://127.0.0.1:%PORT%/health" >>"!DT!\info.txt" 2>&1
>>"!DT!\info.txt" echo.
>>"!DT!\info.txt" echo --- 模型列表 ---
curl -s --max-time 3 "http://127.0.0.1:%PORT%/v1/models" >>"!DT!\info.txt" 2>&1
>>"!DT!\info.txt" echo.
>>"!DT!\info.txt" echo --- ollama ---
ollama list >>"!DT!\info.txt" 2>&1
>>"!DT!\info.txt" echo.
>>"!DT!\info.txt" echo --- 端口 %PORT% ---
netstat -ano | findstr /C:":%PORT% " >>"!DT!\info.txt" 2>&1

if exist "%~dp0data\logs\nexus.log" copy /y "%~dp0data\logs\nexus.log" "!DT!\nexus.log" >nul 2>&1
if exist "%~dp0data\config.json" copy /y "%~dp0data\config.json" "!DT!\config.json" >nul 2>&1

echo.
echo   正在打包 ...
powershell -NoProfile -Command "Compress-Archive -Path '!DT!\*' -DestinationPath '%~dp0诊断报告.zip' -Force" 2>nul
rmdir /s /q "!DT!" 2>nul
if exist "%~dp0诊断报告.zip" (
    echo   [OK] 已生成：%~dp0诊断报告.zip
    echo        请把这个文件发给技术支持。
) else (
    echo   [错误] 打包失败，请手动收集 data\logs\nexus.log
)
goto HOLD

rem =========================================================
:DO_AUTO
echo.
call "%~dp0③ 开机自启.bat"
goto MENU

rem =========================================================
:DO_PORT
echo.
if exist "%~dp0data\config.json" (
    echo   当前配置：%~dp0data\config.json
    findstr /C:"port" "%~dp0data\config.json"
) else (
    echo   配置文件尚未生成，默认端口 %PORT%
)
echo.
echo   [注意] 改端口后，客户端里的接口地址也要跟着改。
echo          自启不受影响（自启只负责启动程序）。
echo.
set "NP="
set /p "NP=   输入新端口（直接回车放弃）: "
if not defined NP goto HOLD
echo !NP! | findstr /R "^[0-9][0-9]*$" >nul 2>&1
if errorlevel 1 (
    echo   [错误] 端口必须是数字。
    goto HOLD
)
if !NP! LSS 1024 (
    echo   [错误] 建议使用 1024 以上端口。
    goto HOLD
)
if not exist "%~dp0data" mkdir "%~dp0data"
powershell -NoProfile -Command "if(Test-Path '%~dp0data\config.json'){$j=Get-Content '%~dp0data\config.json' -Raw|ConvertFrom-Json;$j.server.port=!NP!;$j|ConvertTo-Json -Depth 10|Set-Content '%~dp0data\config.json' -Encoding UTF8}else{'{\"server\":{\"port\":!NP!}}'|Set-Content '%~dp0data\config.json' -Encoding UTF8}" 2>nul
if errorlevel 1 (
    echo   [错误] 写入配置失败。
    goto HOLD
)
echo   [OK] 端口已改为 !NP!
echo        请用菜单第 3 项重启服务生效。
set "PORT=!NP!"
goto HOLD

rem =========================================================
:DO_BACKUP
echo.
if not exist "%~dp0data" (
    echo   [提示] data 目录还不存在，无需备份。
    goto HOLD
)
set "TS=%DATE:~0,4%%DATE:~5,2%%DATE:~8,2%_%TIME:~0,2%%TIME:~3,2%"
set "TS=!TS: =0!"
set "BK=%~dp0data备份_!TS!.zip"
echo   正在打包 data 目录 ...
powershell -NoProfile -Command "Compress-Archive -Path '%~dp0data\*' -DestinationPath '!BK!' -Force" 2>nul
if exist "!BK!" (
    echo   [OK] 已备份到：
    echo        !BK!
) else (
    echo   [错误] 备份失败。
)
goto HOLD

rem =========================================================
rem 公共子程序
rem =========================================================
:FIND_PY
set "PYEXE="
if exist "%TEMP%\nexus_pf.txt" del /q "%TEMP%\nexus_pf.txt" >nul 2>&1
where python >>"%TEMP%\nexus_pf.txt" 2>nul
where py >>"%TEMP%\nexus_pf.txt" 2>nul
for /f "usebackq delims=" %%P in ("%TEMP%\nexus_pf.txt") do (
    if not defined PYEXE call :CHECK_PY "%%P"
)
if exist "%TEMP%\nexus_pf.txt" del /q "%TEMP%\nexus_pf.txt" >nul 2>&1
exit /b 0

:CHECK_PY
echo %~1 | findstr /I "WindowsApps" >nul 2>&1
if not errorlevel 1 exit /b 0
"%~1" -c "import sys" >nul 2>&1
if not errorlevel 1 set "PYEXE=%~1"
exit /b 0

:IS_RUNNING
set "UP=0"
curl -s --max-time 3 "http://127.0.0.1:%PORT%/health" 2>nul | findstr /C:"ok" >nul 2>&1
if not errorlevel 1 set "UP=1"
exit /b 0

:WAIT_UP
set "UP=0"
for /l %%I in (1,1,15) do (
    call :IS_RUNNING
    if "!UP!"=="1" exit /b 0
    timeout /t 1 /nobreak >nul
)
exit /b 0

:HOLD
echo.
pause >nul
goto MENU
