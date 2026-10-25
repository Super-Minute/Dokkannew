@echo off
rem Build the standalone dokkan.exe (run on Windows with Python 3.10+ installed).
py -m pip install --upgrade pyinstaller 2>NUL || python -m pip install --upgrade pyinstaller
py -m PyInstaller --onefile --name dokkan --paths src --clean --noconfirm ^
  --exclude-module pytest --exclude-module tkinter src\dokkan\cli\main.py 2>NUL || ^
python -m PyInstaller --onefile --name dokkan --paths src --clean --noconfirm ^
  --exclude-module pytest --exclude-module tkinter src\dokkan\cli\main.py
echo built: dist\dokkan.exe
