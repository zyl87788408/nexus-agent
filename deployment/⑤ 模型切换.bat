@echo off
chcp 936 >nul
setlocal EnableDelayedExpansion
title NEXUS 引擎 - 模型切换
color 0B

cd /d "%~dp0"

echo ==============================================================
echo              NEXUS 引擎 - 模型切换（悬浮窗）
echo ==============================================================
echo.
echo   打开一个小窗口，三个按钮切换模型来源：
echo.
echo     [本地]  只用自己的电脑，绝不联网
echo     [自动]  优先本地；本地不可用时自动切 API   ^<-- 推荐
echo     [API]   始终用云端 API
echo.
echo   窗口可拖动，始终置顶，点右上角 X 关闭。
echo.

if not exist "悬浮窗.py" (
    echo   [错误] 缺少 悬浮窗.py ，请确认与本脚本在同一目录。
    pause >nul
    exit /b 1
)

rem ---------- 找可用 Python ----------
rem WindowsApps 下的 python.exe 只是应用商店存根，必须实际执行验证
set "PYEXE="
if exist "%TEMP%\nexus_pyfind.txt" del /q "%TEMP%\nexus_pyfind.txt" >nul 2>&1
where python >>"%TEMP%\nexus_pyfind.txt" 2>nul
where py >>"%TEMP%\nexus_pyfind.txt" 2>nul
for /f "usebackq delims=" %%P in ("%TEMP%\nexus_pyfind.txt") do (
    if not defined PYEXE call :TRY_PY "%%P"
)
if exist "%TEMP%\nexus_pyfind.txt" del /q "%TEMP%\nexus_pyfind.txt" >nul 2>&1

if not defined PYEXE (
    echo   [错误] 未找到可用的 Python ，请先运行 ② 一键部署.bat。
    pause >nul
    exit /b 1
)

rem ---------- 确认引擎在跑 ----------
rem 悬浮窗只是遥控器，引擎没起来的话点了也没用，先说清楚
set "PORT=8085"
if exist "data\config.json" (
    for /f "usebackq delims=" %%Q in (`powershell -NoProfile -Command "try{(Get-Content 'data\config.json' -Raw|ConvertFrom-Json).server.port}catch{''}"`) do (
        if not "%%Q"=="" set "PORT=%%Q"
    )
)

powershell -NoProfile -Command "try{$r=Invoke-WebRequest 'http://127.0.0.1:!PORT!/health' -TimeoutSec 3 -UseBasicParsing; exit 0}catch{exit 1}" >nul 2>&1
if errorlevel 1 (
    echo   [提示] 引擎当前未运行（端口 !PORT! 无响应）。
    echo          悬浮窗仍会打开，但按钮切换不会生效。
    echo          请先双击 ④ 常用工具.bat 选 1 启动服务。
    echo.
)

echo   [OK] 正在打开悬浮窗…
start "" "!PYEXE!" "%~dp0悬浮窗.py"
timeout /t 2 >nul
exit /b 0

:TRY_PY
echo %~1 | findstr /I "WindowsApps" >nul 2>&1
if not errorlevel 1 exit /b 0
"%~1" -c "import sys" >nul 2>&1
if not errorlevel 1 set "PYEXE=%~1"
exit /b 0
