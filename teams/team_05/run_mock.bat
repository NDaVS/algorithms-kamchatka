@echo off
chcp 65001 >nul
setlocal
set "ROOT=%~dp0"
if not exist "%ROOT%frontend\node_modules" (echo Сначала запустите setup.bat & pause & exit /b 1)
echo Запускаю только фронтенд в демо-режиме (без бэкенда)...
start "Snegoplan frontend (mock)" cmd /k "cd /d "%ROOT%frontend" && npm run dev -- --host"
timeout /t 5 /nobreak >nul
start "" "http://localhost:5173/dispatcher?mock=1"
echo Водитель (мок): http://localhost:5173/driver?v=П-01^&mock=1
endlocal
