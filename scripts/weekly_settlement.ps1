Set-Location "C:\Users\luisf\OneDrive\Desktop\service_nodo"

if (-not (Test-Path "logs")) {
    New-Item -ItemType Directory -Path "logs" | Out-Null
}

python manage.py generate_weekly_settlements >> logs\weekly_settlement.log 2>&1
