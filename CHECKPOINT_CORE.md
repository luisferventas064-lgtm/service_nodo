# CHECKPOINT CORE – service_nodo (VALIDADO)

## Stack
- Python + Django 5.2.11
- SQL Server SQLEXPRESS
- ODBC Driver 17
- Proyecto: service_nodo

## Migraciones (OK)
- providers: 0001 → 0008
- service_type: 0001 → 0003
- jobs: 0001 → 0006
- assignments: 0001 → 0003
- makemigrations --check --dry-run: No changes detected

## Tablas reales SQL (core)
- provider
- provider_service_area
- provider_service_type
- service_type
- service_skill
- jobs_job
- job_assignment
- client
- worker

## FK confirmadas
- service_skill.service_type_id → service_type.service_type_id
- provider_service_type.provider_id → provider.provider_id
- provider_service_type.service_type_id → service_type.service_type_id
- provider_service_area.provider_id → provider.provider_id

## Núcleo híbrido
Provider
- ProviderServiceType → ServiceType → ServiceSkill
- ProviderServiceArea

Job → JobAssignment → Provider

## Estado actual
- Sistema estable
- Sin errores
- Sin migraciones pendientes
- Sin tablas fantasma
- CRUD funcionando
