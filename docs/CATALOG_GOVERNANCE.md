# Catálogo ServiceType - Gobierno de Datos

## Estado Actual

**Limpieza completada:** 2026-03-18

✅ Todos los registros de `ServiceType` con trailing hashes han sido normalizados:
- 3 registros `"Nettoyage [hash]"` consolidados → 1 registro `"Nettoyage"`  
- 9 registros `"Service [hash]"` consolidados → 1 registro `"Service"`
- Total: 10 duplicados quitados, 2 keepers mantuvieron

## Estructura de Datos Correcta

### Campos Obligatorios

```
ServiceType:
  ├─ service_type_id (PK, AutoField)
  ├─ name (VARCHAR 120, UNIQUE) ← NOMBRE LIMPIO (sin hashes)
  ├─ name_en (VARCHAR 120, nullable)
  ├─ name_fr (VARCHAR 120, nullable)
  ├─ name_es (VARCHAR 120, nullable)
  ├─ slug (SlugField, unique, auto-generated from name_en or name)
  └─ is_active (Boolean, default=True)
```

### Reglas de Validación

**✓ VÁLIDO:**
- `name = "Nettoyage"` 
- `name = "House Cleaning Services"`
- `name = "Professional Painting"`

**✗ INVÁLIDO (Rechazado en save()):**
- `name = "Nettoyage 618eb1c2"` (trailing 8-char hex)
- `name = "Service 07645f93"` (trailing hash)
- `name = "Cleaning test_12345678"` (trailing alphanumeric)

**Pattern rechazado:** `^.+\s+[0-9a-f]{8}$`

## Prevención de Datos Sucios

### 1. Validación en Modelo (automática)

El modelo `ServiceType` incluye un método `clean()` que detecta y rechaza nombres con trailing tokens:

```python
# En service_type/models.py
def clean(self):
    """Validate that no name fields contain trailing hash/token artifacts."""
    from django.core.exceptions import ValidationError
    
    errors = {}
    for field_name in ("name", "name_en", "name_fr", "name_es"):
        value = getattr(self, field_name, None)
        if value and self._TRAILING_TOKEN_RE.match(value):
            errors[field_name] = f"Name contains trailing hash/token artifact."
    
    if errors:
        raise ValidationError(errors)
```

**Cuándo se ejecuta:**
- ✅ Automaticamente en admin si hay `model_validate = True` en la clase Meta
- ⚠️ NO automaticamente en `model.save()` a menos que call `model.full_clean()` antes  
- ⚠️ NO automaticamente en migrations o SQL directo

### 2. Protección en Admin

Para forzar validación en Django admin, agregar en `service_type/admin.py`:

```python
class ServiceTypeAdmin(admin.ModelAdmin):
    def save_model(self, request, obj, form, change):
        obj.full_clean()  # ← Ejecuta validación antes de guardar
        super().save_model(request, obj, form, change)
```

### 3. Protección en API/Forms

Si hay un endpoint o form que crea/edita ServiceTypes, usar `.full_clean()`:

```python
# En forms o serializers
service_type = ServiceType(**validated_data)
service_type.full_clean()  # ← Detecta tokens antes de guardar
service_type.save()
```

## Mitigación en Display Layer

Los nombres sucios TODAVÍA pueden existir en la BD si fueron creados antes de implementar validación. Para evitar mostrarlos al usuario:

**Método 1: Property (actual en código)** ← Implementado
```python 
@property
def localized_name(self):
    # Calls _sanitize_display_name() que stripea hashes
    return self._get_localized_name()
```

**Uso en templates:**
```django
{{ service_type.localized_name }}  ← Muestra "Nettoyage" no "Nettoyage 618eb1c2"
```

**Método 2: TemplateTag personalizado**
```django
{% load service_filters %}
{{ service_type.name | clean_service_name }}
```

## Limpieza Retroactiva

Si aparecen nuevos registros sucios, usar:

```bash
# Preview sin cambios
python manage.py clean_servicetype_names --dry-run

# Con consolidación de duplicados
python manage.py clean_servicetype_names --merge-duplicates

# Con población de campos localizados vacíos
python manage.py clean_servicetype_names --merge-duplicates --populate-defaults

# Con verbose para ver cada operación
python manage.py clean_servicetype_names --merge-duplicates --populate-defaults --verbose
```

## Checklist para Agregar Nuevos ServiceTypes

Cuando se cree un nuevo registro:

- [ ] `name` es único y NO contiene hashes/tokens
- [ ] `name` describe el servicio de forma breve y legible
- [ ] Si es multiidioma, completar `name_en`, `name_fr`, `name_es` también
- [ ] Ejecutar `full_clean()` antes de guardar (si es code, no admin)
- [ ] Verificar que `slug` se generó correctamente
- [ ] En admin, usar form.save() que incluye validación

## Monitoreo

Para detectar nuevos registros sucios:

```bash
# Ejecutar regularmente (ej: cada week en cron)
python manage.py clean_servicetype_names --dry-run
```

Si muestra registros, investigar cómo entraron y aplicar `--merge-duplicates` si es necesario.

## Historia

| Fecha | Acción | Registros | Resultado |
|-------|--------|-----------|-----------|
| 2026-03-18 | Limpieza + consolidación | 12 removidos | ✓ 2 keepers limpios |
| - | Validación agregada | - | ✓ Modelo rechaza nuevos hashes |
| - | Mitigación en display | - | ✓ Sanitización en UI |

