Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass

cd $PSScriptRoot

Write-Host "Starting NODO server..."

.\.venv\Scripts\python.exe manage.py runserver
