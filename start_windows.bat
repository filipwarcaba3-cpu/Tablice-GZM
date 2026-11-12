@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
where py >nul 2>&1
if not errorlevel 1 (
  set "PY=py -3"
) else (
  where python >nul 2>&1
  if errorlevel 1 (
    echo Nie znaleziono Pythona 3. Zainstaluj Python z python.org i uruchom ponownie.
    pause
    exit /b 1
  )
  set "PY=python"
)
%PY% -m pip install -r requirements-windows.txt
if errorlevel 1 (
  echo Nie udalo sie zainstalowac bibliotek. Sprawdz internet i Pythona.
  pause
  exit /b 1
)
start "" /min powershell -NoProfile -Command "Start-Sleep -Seconds 3; Start-Process 'http://127.0.0.1:5000'"
echo Start serwera: http://127.0.0.1:5000
echo Pierwsze pobieranie GTFS moze trwac kilka minut i wymaga internetu.
%PY% app.py
pause
