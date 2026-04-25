@echo off
REM ComfyDir デバッグ起動 (コンソール窓を表示してログを見る用)
chcp 65001 > nul
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] .venv が見つかりません。先にセットアップを実行してください:
  echo   python -m venv .venv
  echo   .venv\Scripts\python.exe -m pip install -r requirements.txt
  echo.
  pause
  exit /b 1
)

".venv\Scripts\python.exe" run.py

echo.
echo --- サーバが終了しました ---
pause
