@echo off
setlocal
cd /d "%~dp0"
echo ==========================================
echo FRAMS - Windows setup and run (Python 3.12)
echo ==========================================
where py >nul 2>nul
if errorlevel 1 (
  echo ERROR: Python Launcher was not found. Install Python 3.12 and enable the launcher.
  pause
  exit /b 1
)
py -3.12 --version
if errorlevel 1 (
  echo ERROR: Python 3.12 is not installed.
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment...
  py -3.12 -m venv .venv
  if errorlevel 1 goto failed
)
echo Upgrading pip...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto failed
echo Installing project dependencies. dlib/face-recognition may require Windows-compatible build tools.
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo Dependency installation failed. Read README.md for the Windows dlib/face-recognition note.
  goto failed
)
echo.
echo Set up or reset the administrator account now.
".venv\Scripts\python.exe" setup_admin.py
if errorlevel 1 goto failed
echo.
echo Starting FRAMS at http://127.0.0.1:5000
".venv\Scripts\python.exe" app.py
goto end
:failed
echo.
echo Setup stopped because an earlier step failed. Copy the error text before asking for help.
pause
exit /b 1
:end
pause
