@echo off
setlocal

cd /d C:\Users\luisf\OneDrive\Desktop\service_nodo

.\.venv\Scripts\python.exe manage.py tick_all

set EXIT_CODE=%ERRORLEVEL%
endlocal & exit /b %EXIT_CODE%
