@echo off
chcp 65001 >nul
set "SCRIPT=%~dp0magnet_download.py"

where python >nul 2>nul
if errorlevel 1 (
    echo 未找到 Python，请先安装 Python 3.9 或更高版本，并勾选"Add Python to PATH"。
    echo 然后运行: pip install libtorrent
    pause
    exit /b 1
)

if "%~1"=="" (
    echo 用法: 把磁力链接或 .torrent 文件拖到本 bat 图标上即可
    echo 或者: 运行下载.bat "magnet:?xt=urn:btih:..." D:\下载
    echo.
    pause
    exit /b
)

if "%~2"=="" (
    python "%SCRIPT%" "%~1"
) else (
    python "%SCRIPT%" "%~1" "%~2"
)
echo.
pause
