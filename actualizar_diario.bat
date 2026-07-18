@echo off
chcp 65001 >nul
cd /d "%~dp0"
python actualizar_diario.py
