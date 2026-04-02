@echo off
chcp 65001 >nul
echo 실험 서버를 시작합니다...
echo.
python web.py
echo.
echo 서버가 종료되었습니다.
pause
