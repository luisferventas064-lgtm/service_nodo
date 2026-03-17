@echo off
cd /d %~dp0

echo Starting NODO server...

.\.venv\Scripts\python.exe manage.py runserver

pause
