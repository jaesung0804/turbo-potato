@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto run
where py >nul 2>nul
if not errorlevel 1 (
  py -3 -m venv .venv
) else (
  where python >nul 2>nul
  if errorlevel 1 goto missing
  python -m venv .venv
)
if errorlevel 1 exit /b 1
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 exit /b 1
goto run
:missing
  echo Python 3.12 이상을 python.org에서 설치한 후 다시 실행하세요.
  pause
  exit /b 1
:run
.venv\Scripts\python.exe -X utf8 fill_workbook.py %*
pause
