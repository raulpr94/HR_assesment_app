@echo off
REM Start the app on Windows. Extra arguments go to streamlit (e.g. --server.port 8502).
setlocal
cd /d "%~dp0\.."

if not exist ".venv\Scripts\activate.bat" (
  echo ERROR: No virtual environment found. Run scripts\setup.bat first.
  exit /b 1
)
call ".venv\Scripts\activate.bat"
python manage.py run %*
endlocal
