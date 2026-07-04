@echo off
cd /d "%~dp0"
where bash >nul 2>&1
if errorlevel 1 (
  echo 未找到 bash，请安装 Git Bash 或将 bash 加入 PATH
  pause
  exit /b 1
)
bash scripts/run_backfill_6years.sh
pause
