@echo off
chcp 65001 >nul
setlocal
set "ROOT=%~dp0"
set "PY=%ROOT%backend\.venv\Scripts\python.exe"
if not exist "%PY%" (echo Сначала запустите setup.bat & pause & exit /b 1)
pushd "%ROOT%backend"
echo === Подготовка данных: реальный OSM, при ошибке - синтетический город ===
"%PY%" -m scripts.prepare_data
if errorlevel 1 (
  echo [!] Не удалось подготовить данные из OSM. Генерирую синтетический город...
  "%PY%" -m scripts.prepare_data --synthetic
  if errorlevel 1 (
    echo.
    echo [ОШИБКА] Подготовка данных не удалась. Подробности: %ROOT%logs\prepare_data.log
    popd
    pause
    exit /b 1
  )
)
popd
echo.
echo Готово. Лог: %ROOT%logs\prepare_data.log
echo Запускайте run.bat
pause
