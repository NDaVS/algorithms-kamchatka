@echo off
chcp 65001 >nul
setlocal
set "ROOT=%~dp0"
cd /d "%ROOT%"
echo === Snegoplan: setup ===
where python >nul 2>nul || (echo [ОШИБКА] Python не найден. Установите Python 3.11+ и отметьте "Add to PATH". & pause & exit /b 1)
where npm >nul 2>nul || (echo [ОШИБКА] Node.js/npm не найден. Установите Node 20+. & pause & exit /b 1)

if not exist "%ROOT%backend\.venv\Scripts\python.exe" (
  echo [1/3] Создаю виртуальное окружение...
  python -m venv "%ROOT%backend\.venv" || (echo [ОШИБКА] venv & pause & exit /b 1)
)
echo [2/3] Устанавливаю Python-зависимости...
"%ROOT%backend\.venv\Scripts\python.exe" -m pip install --upgrade pip
"%ROOT%backend\.venv\Scripts\python.exe" -m pip install -r "%ROOT%backend\requirements.txt"
if errorlevel 1 (
  echo.
  echo [ВНИМАНИЕ] Установка некоторых пакетов не удалась ^(osmnx/geopandas/rasterio нужны только для загрузки OSM^).
  echo Минимум для работы: pip install fastapi uvicorn[standard] networkx numpy scipy pydantic
  "%ROOT%backend\.venv\Scripts\python.exe" -m pip install fastapi "uvicorn[standard]" networkx numpy scipy pydantic requests pytest httpx
  if errorlevel 1 (echo [ОШИБКА] Не удалось установить минимальные зависимости. & pause & exit /b 1)
)
echo [3/3] Устанавливаю npm-зависимости...
pushd "%ROOT%frontend"
call npm install
if errorlevel 1 (echo [ОШИБКА] npm install не удался. & popd & pause & exit /b 1)
popd
echo.
echo Готово. Дальше: prepare_data.bat, затем run.bat
pause
