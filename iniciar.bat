@echo off
chcp 65001 >nul
title CasaMusa — Dashboard de Gerencia
color 0A

echo.
echo  ╔══════════════════════════════════════╗
echo  ║   CASAMUSA — Dashboard de Gerencia   ║
echo  ╚══════════════════════════════════════╝
echo.

:: Verificar Python
python --version >nul 2>&1
if errorlevel 1 (
    echo  ERROR: Python no está instalado.
    echo  Descárgalo en: https://www.python.org/downloads/
    pause
    exit
)

:: Instalar dependencias si faltan
echo  Verificando dependencias...
pip install flask pandas openpyxl pyarrow python-calamine -q --disable-pip-version-check

echo.
echo  Iniciando servidor...
echo  Abre tu navegador en: http://localhost:5000
echo.
echo  (No cierres esta ventana mientras usas el dashboard)
echo.

:: Abrir navegador automáticamente
start "" http://localhost:5000

:: Correr la app
python app.py

pause
