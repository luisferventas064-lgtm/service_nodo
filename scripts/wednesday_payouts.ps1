Set-Location "C:\Users\luisf\OneDrive\Desktop\service_nodo"

if (-not (Test-Path "logs")) {
    New-Item -ItemType Directory -Path "logs" | Out-Null
}

python manage.py generate_wednesday_payouts >> logs\wednesday_payouts.log 2>&1
