@echo off
rem Builds "Fortnite Offline Server.exe" in the project folder (needs: pip install pyinstaller pillow)
cd /d "%~dp0"
python make_icon.py || exit /b 1
python -m PyInstaller --noconfirm --onefile --noconsole --name "Fortnite Offline Server" ^
  --icon "%~dp0icon.ico" --add-data "%~dp0ui;ui" --add-data "%~dp0icon.png;." ^
  --distpath .. --workpath build --specpath build fortnite_offline_server.pyw
