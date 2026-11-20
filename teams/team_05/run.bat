@echo off
chcp 65001 >nul
setlocal
set "ROOT=%~dp0"
set "PY=%ROOT%backend\.venv\Scripts\python.exe"
if not exist "%PY%" (echo Сначала запустите setup.bat & pause & exit /b 1)
if not exist "%ROOT%frontend\node_modules" (echo Сначала запустите setup.bat & pause & exit /b 1)

echo Логи: %ROOT%logs\backend.log, %ROOT%logs\frontend.log
echo Запускаю бэкенд (порт 8000) и фронтенд (порт 5173)...
start "Snegoplan backend" cmd /k "cd /d "%ROOT%backend" && "%PY%" -m uvicorn app.main:app --host 0.0.0.0 --port 8000 || (echo. & echo [ОШИБКА] Бэкенд завершился с ошибкой. Лог: %ROOT%logs\backend.log & pause)"
start "Snegoplan frontend" cmd /k "cd /d "%ROOT%frontend" && npm run dev -- --host || pause"

echo.
echo ================================================================
echo  Диспетчер:  http://localhost:5173/dispatcher
echo  Водитель с телефона (тот же Wi-Fi): http://IP-КОМПЬЮТЕРА:5173/driver?v=П-01
echo  IP компьютера: выполните  ipconfig  и найдите "IPv4-адрес".
echo  Если телефон не открывает страницу - разрешите порт 5173 в брандмауэре Windows.
echo ================================================================
timeout /t 6 /nobreak >nul
start "" "http://localhost:5173/dispatcher"
endlocal
