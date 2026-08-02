@echo off
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  py -3.11 -m venv .venv
)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
pyinstaller --noconfirm --clean --onefile --windowed --name QRBeam app.py
if errorlevel 1 exit /b 1
echo.
echo Built: %CD%\dist\QRBeam.exe
endlocal
