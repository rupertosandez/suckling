@echo off
setlocal
cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" (
    echo venv\Scripts\python.exe was not found.
    exit /b 1
)

venv\Scripts\python.exe -m PyInstaller --clean --noconfirm Suckling.spec
if errorlevel 1 exit /b %errorlevel%

copy /Y "dist\Suckling.exe" "Suckling.exe" >nul
echo built Suckling.exe
