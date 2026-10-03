@echo off
rem 双击这个就能启动（需要已装 Python 和 PySide6）
rem 首次使用请先运行： pip install -r requirements.txt
cd /d "%~dp0"
python main_window.py
if errorlevel 1 (
  echo.
  echo 启动失败。可能是没装 PySide6，试试：
  echo     pip install -r requirements.txt
  pause
)
