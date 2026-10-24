@echo off
REM Lint, format check and tests on Windows. Extra arguments go to pytest.
setlocal
cd /d "%~dp0\.."

if not exist ".venv\Scripts\activate.bat" (
  echo ERROR: No virtual environment found. Run scripts\setup.bat first.
  exit /b 1
)
call ".venv\Scripts\activate.bat"

echo ==^> ruff check
ruff check .
if errorlevel 1 exit /b 1
echo ==^> ruff format --check
ruff format --check .
if errorlevel 1 exit /b 1
echo ==^> pytest
pytest %*
if errorlevel 1 exit /b 1
endlocal
