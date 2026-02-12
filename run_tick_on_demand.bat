@echo off
cd /d C:\Users\luisf\OneDrive\Desktop\service_nodo
call .venv\Scripts\activate
python manage.py tick_on_demand
