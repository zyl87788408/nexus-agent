@echo off
chcp 936 >nul
setlocal EnableDelayedExpansion
title NEXUS 引擎 - 部署前完整检测
color 0B

cd /d "%~dp0"

set "PORT=8085"
set "MEM_MIN=8"
set "MEM_REC=16"
set "DISK_MIN=10"
set "MIRROR=https://pypi.tuna.tsinghua.edu.cn/simple/"

set "BLOCK=0"
set "B_SYS=0"
set "B_PY=0"
set "B_PIP=0"
set "B_MEM=0"
set "B_DISK=0"
set "B_NET=0"
set "B_FILE=0"
set "W_ADMIN=0"
set "W_AV=0"
set "W_PORT=0"
set "W_MEM=0"
set "W_NET=0"
set "W_SYS=0"

echo ==============================================================
echo             NEXUS 引擎 - 部署前完整检测
echo ==============================================================
echo   本脚本检查全部会导致安装失败的因素。
echo   预计耗时 30 秒，期间请勿关闭窗口。
echo.

rem ============ 1/10 操作系统 ============
echo [1/10] 操作系统版本与架构 ...
set "OSBUILD="
set "OSBITS="
powershell -NoProfile -Command "[Environment]::OSVersion.Version.Build" >"%TEMP%\nexus_build.txt" 2>nul
if exist "%TEMP%\nexus_build.txt" for /f "usebackq delims=" %%B in ("%TEMP%\nexus_build.txt") do set "OSBUILD=%%B"
if exist "%TEMP%\nexus_build.txt" del /q "%TEMP%\nexus_build.txt" >nul 2>&1

powershell -NoProfile -Command "if([Environment]::Is64BitOperatingSystem){'64'}else{'32'}" >"%TEMP%\nexus_x64.txt" 2>nul
if exist "%TEMP%\nexus_x64.txt" for /f "usebackq delims=" %%B in ("%TEMP%\nexus_x64.txt") do set "OSBITS=%%B"
if exist "%TEMP%\nexus_x64.txt" del /q "%TEMP%\nexus_x64.txt" >nul 2>&1

if not defined OSBUILD (
    echo    [!] 无法读取系统版本，该项跳过
    set "W_SYS=1"
) else (
    if !OSBUILD! LSS 17134 (
        echo    [X] 系统 Build !OSBUILD! 过低，需 Windows 10 1803 及以上
        set "B_SYS=1"
    ) else (
        if !OSBUILD! LSS 22000 (
            echo    [OK] Windows 10 Build !OSBUILD!
        ) else (
            echo    [OK] Windows 11 Build !OSBUILD!
        )
    )
)
if "!OSBITS!"=="32" (
    echo    [X] 32 位操作系统，NEXUS 需要 64 位
    set "B_SYS=1"
) else (
    if defined OSBITS echo    [OK] 64 位操作系统
)
echo.

rem ============ 2/10 Python ============
echo [2/10] Python 版本与位数 ...
set "PYCMD="
set "PYVER="
set "PYBITS="
if exist "%TEMP%\nexus_py.txt" del /q "%TEMP%\nexus_py.txt" >nul 2>&1

python -c "import sys;print(str(sys.version_info[0])+'.'+str(sys.version_info[1]))" >"%TEMP%\nexus_py.txt" 2>nul
if not errorlevel 1 set "PYCMD=python"
if not defined PYCMD (
    py -3 -c "import sys;print(str(sys.version_info[0])+'.'+str(sys.version_info[1]))" >"%TEMP%\nexus_py.txt" 2>nul
    if not errorlevel 1 set "PYCMD=py -3"
)
if defined PYCMD if exist "%TEMP%\nexus_py.txt" for /f "usebackq delims=" %%V in ("%TEMP%\nexus_py.txt") do set "PYVER=%%V"
if exist "%TEMP%\nexus_py.txt" del /q "%TEMP%\nexus_py.txt" >nul 2>&1

if not defined PYCMD (
    echo    [X] 未检测到 Python ，客户必须先安装 Python 3.10 及以上
    set "B_PY=1"
) else (
    !PYCMD! -c "import sys;sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>&1
    if errorlevel 1 (
        echo    [X] Python !PYVER! 版本过低，需 3.10 及以上
        set "B_PY=1"
    ) else (
        echo    [OK] Python !PYVER!
    )
    !PYCMD! -c "import struct;print(struct.calcsize('P')*8)" >"%TEMP%\nexus_bits.txt" 2>nul
    if exist "%TEMP%\nexus_bits.txt" for /f "usebackq delims=" %%B in ("%TEMP%\nexus_bits.txt") do set "PYBITS=%%B"
    if exist "%TEMP%\nexus_bits.txt" del /q "%TEMP%\nexus_bits.txt" >nul 2>&1
    if "!PYBITS!"=="32" (
        echo    [X] 检测到 32 位 Python ，必须换装 64 位
        set "B_PY=1"
    ) else (
        if defined PYBITS echo    [OK] 64 位 Python
    )
)
echo.

rem ============ 3/10 pip ============
echo [3/10] pip 可用性 ...
set "B_PIP=0"
if defined PYCMD (
    !PYCMD! -m pip --version >"%TEMP%\nexus_pip.txt" 2>nul
    if errorlevel 1 (
        echo    [X] pip 不可用，无法安装依赖 fastapi/uvicorn
        set "B_PIP=1"
    ) else (
        set "PIPVER="
        for /f "usebackq delims=" %%P in ("%TEMP%\nexus_pip.txt") do set "PIPVER=%%P"
        echo    [OK] !PIPVER!
    )
    if exist "%TEMP%\nexus_pip.txt" del /q "%TEMP%\nexus_pip.txt" >nul 2>&1
) else (
    echo    [-] 跳过，Python 未就绪
)
echo.

rem ============ 4/10 内存 ============
echo [4/10] 内存 ...
set "MEMGB="
set "MEMAVAIL="
powershell -NoProfile -Command "[math]::Floor((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory/1GB)" >"%TEMP%\nexus_mem.txt" 2>nul
if exist "%TEMP%\nexus_mem.txt" for /f "usebackq delims=" %%M in ("%TEMP%\nexus_mem.txt") do set "MEMGB=%%M"
if exist "%TEMP%\nexus_mem.txt" del /q "%TEMP%\nexus_mem.txt" >nul 2>&1
powershell -NoProfile -Command "[math]::Floor((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory/1MB)" >"%TEMP%\nexus_av.txt" 2>nul
if exist "%TEMP%\nexus_av.txt" for /f "usebackq delims=" %%M in ("%TEMP%\nexus_av.txt") do set "MEMAVAIL=%%M"
if exist "%TEMP%\nexus_av.txt" del /q "%TEMP%\nexus_av.txt" >nul 2>&1

if not defined MEMGB (
    echo    [!] 无法读取内存，该项跳过
) else (
    if !MEMGB! LSS %MEM_MIN% (
        echo    [X] 物理内存 !MEMGB! GB ，低于最低要求 %MEM_MIN% GB
        set "B_MEM=1"
    ) else (
        if !MEMGB! LSS %MEM_REC% (
            echo    [!] 物理内存 !MEMGB! GB ，可装但只能跑小模型
            set "W_MEM=1"
        ) else (
            echo    [OK] 物理内存 !MEMGB! GB
        )
    )
)
if defined MEMAVAIL (
    if !MEMAVAIL! LSS 3 (
        echo    [!] 当前可用内存仅 !MEMAVAIL! GB ，建议关闭其他程序后再部署
        set "W_MEM=1"
    ) else (
        echo    [OK] 当前可用内存 !MEMAVAIL! GB
    )
)
echo.

rem ============ 5/10 磁盘 ============
echo [5/10] 磁盘空间 ...
set "DISKGB="
for /f "usebackq delims=" %%D in (`powershell -NoProfile -Command "[math]::Floor((Get-PSDrive '%~d0'.TrimEnd(':')).Free/1GB)" 2^>nul`) do set "DISKGB=%%D"

if not defined DISKGB (
    echo    [!] 无法读取磁盘空间，该项跳过
) else (
    if !DISKGB! LSS %DISK_MIN% (
        echo    [X] %~d0 剩余 !DISKGB! GB ，低于 %DISK_MIN% GB ，装不下模型
        set "B_DISK=1"
    ) else (
        echo    [OK] %~d0 剩余 !DISKGB! GB
    )
)
echo.

rem ============ 6/10 网络 ============
echo [6/10] 网络连通性 ...
where curl >nul 2>&1
if errorlevel 1 (
    echo    [X] 系统缺少 curl ，无法联网安装依赖
    set "B_NET=1"
) else (
    curl -s -f --max-time 10 -o nul "%MIRROR%" 2>nul
    if errorlevel 1 (
        echo    [X] 无法访问 pip 镜像，依赖装不上
        set "B_NET=1"
    ) else (
        echo    [OK] pip 镜像可访问
    )
    curl -s -f --max-time 8 -o nul "https://ollama.com" 2>nul
    if errorlevel 1 (
        echo    [!] 无法访问 ollama.com ，将无法自动下载模型
        set "W_NET=1"
    ) else (
        echo    [OK] ollama.com 可访问
    )
)
echo.

rem ============ 7/10 权限 ============
echo [7/10] 管理员权限 ...
fltmc >nul 2>&1
if errorlevel 1 (
    echo    [!] 当前不是管理员，部署时可能无法结束占用端口的进程
    echo        建议右键选择“以管理员身份运行”
    set "W_ADMIN=1"
) else (
    echo    [OK] 已具备管理员权限
)
echo.

rem ============ 8/10 安全软件 ============
echo [8/10] 安全软件 ...
set "AVHIT="
for %%A in (360tray.exe 360sd.exe ZhuDongFangYu.exe QQPCTray.exe QQPCRTP.exe
            HipsTray.exe HipsDaemon.exe MsMpEng.exe kxetray.exe
            usysdiag.exe curtail.exe) do (
    tasklist /FI "IMAGENAME eq %%A" /NH 2>nul | find /I "%%A" >nul
    if not errorlevel 1 (
        echo    [!] 检测到 %%A 正在运行
        set "AVHIT=1"
    )
)
if defined AVHIT (
    echo        可能拦截 pip 安装或程序启动，必要时临时关闭或加白名单
    set "W_AV=1"
) else (
    echo    [OK] 未发现常见安全软件
)
echo.

rem ============ 9/10 端口 ============
echo [9/10] 端口占用 ...
set "BUSY="
for %%P in (%PORT% 11434 8080) do (
    netstat -ano | findstr /C:":%%P " | findstr /I "LISTENING" >nul
    if not errorlevel 1 (
        echo    [!] 端口 %%P 已被占用
        set "BUSY=1"
    )
)
if defined BUSY (
    echo        %PORT% 被占用时部署脚本会自动清理；11434/8080 为模型服务端口
    set "W_PORT=1"
) else (
    echo    [OK] %PORT% / 11434 / 8080 均空闲
)
echo.

rem ============ 10/10 程序与后端 ============
echo [10/10] 程序文件与推理后端 ...
if exist nexus.pyz (
    echo    [OK] nexus.pyz 已就位
) else (
    echo    [X] 缺少 nexus.pyz ，压缩包没解压完整或未放在同一目录
    set "B_FILE=1"
)
if not exist requirements.txt (
    echo    [X] 缺少 requirements.txt
    set "B_FILE=1"
)

set "BACKEND=0"
curl -s -f --max-time 4 http://127.0.0.1:11434/api/tags >"%TEMP%\nexus_ollama.txt" 2>nul
if not errorlevel 1 (
    findstr /I /C:name "%TEMP%\nexus_ollama.txt" >nul 2>&1
    if not errorlevel 1 (
        set "BACKEND=1"
        echo    [OK] Ollama 已运行且已装模型
    ) else (
        echo    [!] Ollama 已运行，但还没有下载任何模型
        set "BACKEND=2"
    )
) else (
    echo    [-] 未检测到 Ollama
)
if exist "%TEMP%\nexus_ollama.txt" del /q "%TEMP%\nexus_ollama.txt" >nul 2>&1

curl -s -f --max-time 4 http://127.0.0.1:8080/v1/models >nul 2>&1
if not errorlevel 1 (
    set "BACKEND=1"
    echo    [OK] llama.cpp 已运行
)
echo.

rem ============ 汇总 ============
echo ==============================================================
echo                       检 测 结 论
echo ==============================================================

if "!B_SYS!"=="1" set "BLOCK=1"
if "!B_PY!"=="1" set "BLOCK=1"
if "!B_PIP!"=="1" set "BLOCK=1"
if "!B_MEM!"=="1" set "BLOCK=1"
if "!B_DISK!"=="1" set "BLOCK=1"
if "!B_NET!"=="1" set "BLOCK=1"
if "!B_FILE!"=="1" set "BLOCK=1"

if "!BLOCK!"=="1" (
    echo   结果：不能直接安装，需先解决以下问题
    echo --------------------------------------------------------------
    if "!B_SYS!"=="1"  echo   [阻塞] 操作系统版本或位数不满足，需 64 位 Win10 1803+
    if "!B_PY!"=="1"   echo   [阻塞] Python 未安装 / 版本低于 3.10 / 是 32 位
    if "!B_PIP!"=="1"  echo   [阻塞] pip 不可用，修复命令：python -m ensurepip --upgrade
    if "!B_MEM!"=="1"  echo   [阻塞] 内存不足 8 GB ，需升级配置
    if "!B_DISK!"=="1" echo   [阻塞] 磁盘剩余不足 10 GB ，需清理或换盘
    if "!B_NET!"=="1"  echo   [阻塞] 网络不通或缺少 curl ，依赖装不上
    if "!B_FILE!"=="1" echo   [阻塞] 程序文件缺失，重新解压压缩包
    echo.
    echo   解决以上问题后，重新运行本检测脚本。
) else (
    echo   结果：可以安装
    echo --------------------------------------------------------------
    if "!W_MEM!"=="1"   echo   [注意] 内存偏小或可用内存不足，建议只装小模型
    if "!W_NET!"=="1"   echo   [注意] 无法访问 ollama.com ，模型需手动拷贝
    if "!W_ADMIN!"=="1" echo   [注意] 非管理员权限，部署时若失败请以管理员重跑
    if "!W_AV!"=="1"    echo   [注意] 有安全软件，安装被拦截时需加白名单
    if "!W_PORT!"=="1"  echo   [注意] 有端口被占用，部署脚本会尝试自动处理
    if "!W_SYS!"=="1"   echo   [注意] 系统版本读取失败，已跳过该项
    if "!BACKEND!"=="0" echo   [注意] 未检测到模型服务，部署后脚本会引导安装 Ollama
    if "!BACKEND!"=="2" echo   [注意] Ollama 已装但无模型，部署时会引导下载
    echo.
    echo   下一步：双击 “② 一键部署.bat” 开始安装。
)
echo.

rem ---------- 模型选型参考 ----------
if not "%MEMGB%"=="" (
echo 模型选型参考（本机内存 %MEMGB% GB）：
if !MEMGB! GEQ 32 (
    echo    推荐 qwen2.5:14b   约 9 GB
) else if !MEMGB! GEQ 16 (
    echo    推荐 qwen2.5:7b    约 5 GB
) else (
    echo    推荐 qwen2.5:3b    约 2 GB
)
echo    装大模型会非常慢甚至卡死，宁可选小一号。
echo.
echo    运行 ② 一键部署.bat 时脚本会自动询问是否下载，选 Y 即可。
echo.
)

echo ==============================================================
echo   请把本窗口截图发给技术支持，便于确认环境情况。
echo ==============================================================
pause >nul
exit /b %BLOCK%
