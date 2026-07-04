@echo off
cd /d "%~dp0"
bash scripts/run_backfill_kline_parallel.sh
pause
