@echo off
chcp 936 >nul
setlocal EnableDelayedExpansion
title NEXUS 引擎 - 开机自启设置
color 0B

cd /d "%~dp0"

rem /quiet 由 ② 一键部署.bat 调用，不显示提示、不等按键
set "QUIET=0"
if /I "%~1"=="/quiet" set "QUIET=1"

if "!QUIET!"=="0" (
    echo ==============================================================
    echo              NEXUS 引擎 - 开机自启设置
    echo ==============================================================
    echo.
)

if not exist "nexus.pyz" (
    echo   [错误] 缺少 nexus.pyz ，请确认本脚本与它同一目录。
    call :PAUSE_IF
    exit /b 1
)

set "STARTUP="
for /f "usebackq delims=" %%S in (`powershell -NoProfile -Command "[Environment]::GetFolderPath('Startup')"`) do set "STARTUP=%%S"
if not defined STARTUP (
    echo   [错误] 无法定位启动文件夹。
    echo   请按 Win+R 输入 shell:startup ，手动放入快捷方式。
    call :PAUSE_IF
    exit /b 1
)
set "VBS=%STARTUP%\NEXUS自启.vbs"

rem ---------- 已开启 ----------
if exist "%VBS%" (
    if "!QUIET!"=="1" exit /b 0
    echo   开机自启【已开启】
    echo   !VBS!
    echo.
    set "ANS="
    set /p "ANS=   是否取消？[Y/N]: "
    if /I "!ANS!"=="Y" (
        del /q "!VBS!" >nul 2>&1
        if exist "!VBS!" (
            echo   [错误] 取消失败，可能被安全软件拦截，请手动删除。
        ) else (
            echo   [OK] 已取消，重启后不再自动运行。
        )
    ) else (
        echo   已保持现状。
    )
    echo.
    call :PAUSE_IF
    exit /b 0
)

rem ---------- 找可用 Python ----------
rem WindowsApps 下的 python.exe 只是应用商店存根，运行只会弹商店，
rem 必须实际执行验证，否则开机自启会静默失效
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
    call :PAUSE_IF
    exit /b 1
)
if "!QUIET!"=="0" (
    echo   [OK] Python：!PYEXE!
    echo.
)

rem ---------- 生成启动脚本 ----------
rem 用 python 而非 pythonw：pythonw 下 sys.stdout 为 None ，
rem uvicorn 写日志会抛异常；窗口样式 0 本身就能完全隐藏。
> "%VBS%" echo Set sh = CreateObject("WScript.Shell")
>>"%VBS%" echo sh.CurrentDirectory = "%~dp0."
>>"%VBS%" echo sh.Run """!PYEXE!"" ""%~dp0nexus.pyz""", 0, False

if not exist "%VBS%" (
    echo   [错误] 启动脚本生成失败，可能被安全软件拦截。
    echo   请把本文件夹加入白名单后重试。
    call :PAUSE_IF
    exit /b 1
)

if "!QUIET!"=="1" (
    echo   [OK] 开机自启已设置
    exit /b 0
)

echo   [OK] 已写入启动项：
echo        !VBS!
echo.

rem ---------- 立即验证一次 ----------
echo   正在启动服务验证（已在运行会提示端口占用，属正常）...
start "NEXUS Server" /min "!PYEXE!" "%~dp0nexus.pyz"
timeout /t 12 /nobreak >nul

set "OK=0"
curl -s --max-time 5 "http://127.0.0.1:8085/health" 2>nul | findstr /C:"ok" >nul 2>&1
if not errorlevel 1 set "OK=1"

echo.
if "!OK!"=="1" (
    echo   [OK] 服务运行正常，开机自启已生效。
) else (
    echo   [注意] 服务暂未就绪，首次启动可能较慢。
    echo          请浏览器访问 http://127.0.0.1:8085/health 确认。
)
echo.
echo ==============================================================
echo   设置完成
echo ==============================================================
echo   以后开机自动运行 NEXUS ，不必再双击 ② 一键部署.bat。
echo.
echo   注意：
echo     1. 运行后没有可见窗口，是正常的
echo     2. 要关闭服务：任务管理器结束 python.exe
echo     3. 要取消自启：再双击一次本脚本
echo     4. 移动文件夹后需重新运行本脚本（路径会变）
echo ==============================================================
echo.
call :PAUSE_IF
exit /b 0

:TRY_PY
echo %~1 | findstr /I "WindowsApps" >nul 2>&1
if not errorlevel 1 exit /b 0
"%~1" -c "import sys" >nul 2>&1
if not errorlevel 1 set "PYEXE=%~1"
exit /b 0

:PAUSE_IF
if "!QUIET!"=="1" exit /b 0
pause >nul
exit /b 0
