@echo off
REM Set up the app on Windows: venv, requirements, .env, database, demo data.
REM Safe to re-run: existing venv, .env, database and CSV are kept.
setlocal
cd /d "%~dp0\.."

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
  where python >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo ERROR: Python 3.10+ not found. Install it from https://www.python.org/downloads/
  exit /b 1
)
%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"
if errorlevel 1 (
  echo ERROR: Python 3.10+ is required.
  exit /b 1
)

if exist ".venv\Scripts\python.exe" (
  echo ==^> Reusing existing virtual environment ^(.venv^)
) else (
  echo ==^> Creating virtual environment ^(.venv^)
  %PY% -m venv .venv
  if errorlevel 1 (
    echo ERROR: Could not create the virtual environment.
    exit /b 1
  )
)
call ".venv\Scripts\activate.bat"

echo ==^> Installing requirements
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r requirements.txt
if errorlevel 1 (
  echo ERROR: Installing requirements failed ^(see above^).
  exit /b 1
)

if exist ".env" (
  echo ==^> Keeping existing .env
) else (
  copy /Y ".env.example" ".env" >nul
  echo ==^> Created .env from .env.example
)

echo ==^> Initialising database
python manage.py init-db
if errorlevel 1 exit /b 1
echo ==^> Loading demo data
python manage.py seed-demo
if errorlevel 1 exit /b 1

echo.
echo Setup complete. Start the app with: scripts\run_app.bat
endlocal
