from decimal import Decimal
import random

from django.apps import apps
from django.db import transaction


def model(app_label, model_name):
    return apps.get_model(app_label, model_name)


ServiceType = model("service_type", "ServiceType")
ServiceSkill = model("service_type", "ServiceSkill")
Provider = model("providers", "Provider")
ProviderService = model("providers", "ProviderService")

try:
    ProviderServiceExtra = model("providers", "ProviderServiceExtra")
except LookupError:
    ProviderServiceExtra = None

try:
    ProviderSkillPrice = model("providers", "ProviderSkillPrice")
except LookupError:
    ProviderSkillPrice = None


def field_names(model_cls):
    return {f.name for f in model_cls._meta.get_fields()}


def concrete_field_names(model_cls):
    return {f.name for f in model_cls._meta.fields}


def first_existing(model_cls, candidates):
    names = concrete_field_names(model_cls)
    for c in candidates:
        if c in names:
            return c
    return None


def set_if_exists(obj, **kwargs):
    names = concrete_field_names(obj.__class__)
    for k, v in kwargs.items():
        if k in names:
            setattr(obj, k, v)


def get_or_create_by_lookup(model_cls, lookup=None, defaults=None):
    lookup = lookup or {}
    defaults = defaults or {}
    obj, created = model_cls.objects.get_or_create(**lookup, defaults=defaults)
    return obj, created


SERVICE_SKILL_MAP = {
    "CLEAN": [
        "Deep Cleaning",
        "Move In / Move Out Cleaning",
        "Office Cleaning",
    ],
    "PLUMB": [
        "Leak Repair",
        "Drain Cleaning",
        "Fixture Installation",
    ],
    "ELEC": [
        "Light Fixture Installation",
        "Outlet / Switch Repair",
        "Breaker / Panel Inspection",
    ],
}

EXTRAS_MAP = {
    "Deep Cleaning": [
        ("Inside Fridge", Decimal("25.00")),
        ("Inside Oven", Decimal("30.00")),
        ("Interior Windows", Decimal("40.00")),
    ],
    "Move In / Move Out Cleaning": [
        ("Inside Cabinets", Decimal("35.00")),
        ("Wall Spot Cleaning", Decimal("20.00")),
        ("Balcony Cleaning", Decimal("25.00")),
    ],
    "Office Cleaning": [
        ("Conference Room Detail", Decimal("30.00")),
        ("Kitchenette Detail", Decimal("25.00")),
        ("Trash + Recycling Handling", Decimal("15.00")),
    ],
    "Leak Repair": [
        ("Emergency Visit", Decimal("75.00")),
        ("Small Parts Kit", Decimal("25.00")),
    ],
    "Drain Cleaning": [
        ("Camera Inspection", Decimal("90.00")),
        ("Secondary Drain", Decimal("35.00")),
    ],
    "Fixture Installation": [
        ("Old Fixture Removal", Decimal("25.00")),
        ("Sealant Finish", Decimal("15.00")),
    ],
    "Light Fixture Installation": [
        ("High Ceiling Access", Decimal("45.00")),
        ("Old Fixture Removal", Decimal("20.00")),
    ],
    "Outlet / Switch Repair": [
        ("Same-Day Visit", Decimal("50.00")),
        ("Faceplate Replacement", Decimal("10.00")),
    ],
    "Breaker / Panel Inspection": [
        ("Detailed Report", Decimal("35.00")),
        ("Urgent Scheduling", Decimal("40.00")),
    ],
}


def find_service_type_by_code(code):
    code_field = first_existing(ServiceType, ["code", "slug", "key"])
    if code_field:
        return ServiceType.objects.filter(**{code_field: code}).first()

    name_map = {
        "CLEAN": "Cleaning",
        "PLUMB": "Plumbing",
        "ELEC": "Electrical",
    }
    name_field = first_existing(ServiceType, ["name", "title"])
    if name_field:
        return ServiceType.objects.filter(**{name_field: name_map[code]}).first()
    return None


def create_service_skill(service_type_obj, skill_name, order_num):
    st_fields = concrete_field_names(ServiceSkill)

    lookup = {}
    defaults = {}

    name_field = first_existing(ServiceSkill, ["name", "title"])
    code_field = first_existing(ServiceSkill, ["code", "slug", "key"])
    service_type_fk = first_existing(ServiceSkill, ["service_type", "service", "service_type_id"])

    if service_type_fk == "service_type_id":
        lookup["service_type_id"] = service_type_obj.id
    elif service_type_fk:
        lookup[service_type_fk] = service_type_obj

    if name_field:
        lookup[name_field] = skill_name

    if code_field:
        defaults[code_field] = (
            skill_name.upper()
            .replace(" / ", "_")
            .replace("/", "_")
            .replace(" ", "_")
            .replace("-", "_")
        )

    if "is_active" in st_fields:
        defaults["is_active"] = True
    if "active" in st_fields:
        defaults["active"] = True
    if "display_order" in st_fields:
        defaults["display_order"] = order_num
    if "sort_order" in st_fields:
        defaults["sort_order"] = order_num
    if "order" in st_fields:
        defaults["order"] = order_num

    if not lookup:
        raise RuntimeError("No se pudo construir lookup para ServiceSkill")

    obj, created = get_or_create_by_lookup(ServiceSkill, lookup=lookup, defaults=defaults)
    return obj, created


def _skill_label(skill_obj):
    skill_name_field = first_existing(ServiceSkill, ["name", "title"])
    if skill_name_field:
        return getattr(skill_obj, skill_name_field)
    return str(skill_obj)


def create_provider_service(provider_obj, service_type_obj, skill_obj, base_price):
    ps_fields = concrete_field_names(ProviderService)

    lookup = {}
    defaults = {}
    skill_label = _skill_label(skill_obj)

    provider_fk = first_existing(ProviderService, ["provider", "provider_id"])
    service_type_fk = first_existing(ProviderService, ["service_type", "service", "service_type_id", "service_id"])
    skill_fk = first_existing(ProviderService, ["service_skill", "skill", "service_skill_id", "skill_id"])
    custom_name_field = first_existing(ProviderService, ["custom_name", "name", "title"])

    if provider_fk == "provider_id":
        lookup["provider_id"] = provider_obj.pk
    elif provider_fk:
        lookup[provider_fk] = provider_obj

    if service_type_fk == "service_type_id":
        lookup["service_type_id"] = service_type_obj.pk
    elif service_type_fk == "service_id":
        lookup["service_id"] = service_type_obj.pk
    elif service_type_fk:
        lookup[service_type_fk] = service_type_obj

    if skill_fk == "service_skill_id":
        lookup["service_skill_id"] = skill_obj.pk
    elif skill_fk == "skill_id":
        lookup["skill_id"] = skill_obj.pk
    elif skill_fk:
        lookup[skill_fk] = skill_obj

    if custom_name_field:
        lookup[custom_name_field] = skill_label

    if "active" in ps_fields:
        defaults["active"] = True
    if "is_active" in ps_fields:
        defaults["is_active"] = True
    if "is_enabled" in ps_fields:
        defaults["is_enabled"] = True
    if "description" in ps_fields:
        defaults["description"] = f"Seeded offer for {skill_label}"
    if "billing_unit" in ps_fields:
        defaults["billing_unit"] = "fixed"

    price_candidates = [
        "price_cents",
        "base_price",
        "unit_price",
        "starting_price",
        "price",
        "min_price",
        "hourly_rate",
    ]
    for pf in price_candidates:
        if pf in ps_fields:
            defaults[pf] = int(base_price * 100) if pf == "price_cents" else base_price
            break

    obj, created = get_or_create_by_lookup(ProviderService, lookup=lookup, defaults=defaults)
    return obj, created


def create_provider_skill_price(provider_obj, provider_service_obj, skill_obj, price_value):
    if ProviderSkillPrice is None:
        return None, False

    psp_fields = concrete_field_names(ProviderSkillPrice)
    lookup = {}
    defaults = {}

    provider_fk = first_existing(ProviderSkillPrice, ["provider", "provider_id"])
    ps_fk = first_existing(ProviderSkillPrice, ["provider_service", "provider_service_id"])
    skill_fk = first_existing(ProviderSkillPrice, ["service_skill", "skill", "service_skill_id", "skill_id"])

    if provider_fk == "provider_id":
        lookup["provider_id"] = provider_obj.pk
    elif provider_fk:
        lookup[provider_fk] = provider_obj

    if ps_fk == "provider_service_id":
        lookup["provider_service_id"] = provider_service_obj.pk
    elif ps_fk:
        lookup[ps_fk] = provider_service_obj

    if skill_fk == "service_skill_id":
        lookup["service_skill_id"] = skill_obj.pk
    elif skill_fk == "skill_id":
        lookup["skill_id"] = skill_obj.pk
    elif skill_fk:
        lookup[skill_fk] = skill_obj

    for pf in ["price_amount", "price", "unit_price", "base_price", "amount"]:
        if pf in psp_fields:
            defaults[pf] = price_value
            break

    if "active" in psp_fields:
        defaults["active"] = True
    if "is_active" in psp_fields:
        defaults["is_active"] = True
    if "pricing_unit" in psp_fields:
        defaults["pricing_unit"] = "fixed"
    if "currency_code" in psp_fields:
        defaults["currency_code"] = "CAD"

    if not lookup:
        return None, False

    obj, created = get_or_create_by_lookup(ProviderSkillPrice, lookup=lookup, defaults=defaults)
    return obj, created


def create_provider_service_extra(provider_service_obj, extra_name, price_value, order_num):
    if ProviderServiceExtra is None:
        return None, False

    pse_fields = concrete_field_names(ProviderServiceExtra)
    lookup = {}
    defaults = {}

    ps_fk = first_existing(ProviderServiceExtra, ["provider_service", "provider_service_id"])
    if ps_fk == "provider_service_id":
        lookup["provider_service_id"] = provider_service_obj.pk
    elif ps_fk:
        lookup[ps_fk] = provider_service_obj

    name_field = first_existing(ProviderServiceExtra, ["name", "title"])
    if name_field:
        lookup[name_field] = extra_name

    code_field = first_existing(ProviderServiceExtra, ["code", "slug", "key"])
    if code_field:
        defaults[code_field] = (
            extra_name.upper()
            .replace(" ", "_")
            .replace("/", "_")
            .replace("-", "_")
        )

    for pf in ["price", "unit_price", "extra_price", "amount"]:
        if pf in pse_fields:
            defaults[pf] = price_value
            break

    if "active" in pse_fields:
        defaults["active"] = True
    if "is_active" in pse_fields:
        defaults["is_active"] = True
    if "is_optional" in pse_fields:
        defaults["is_optional"] = True
    if "display_order" in pse_fields:
        defaults["display_order"] = order_num
    if "sort_order" in pse_fields:
        defaults["sort_order"] = order_num
    if "order" in pse_fields:
        defaults["order"] = order_num

    if not lookup:
        return None, False

    obj, created = get_or_create_by_lookup(ProviderServiceExtra, lookup=lookup, defaults=defaults)
    return obj, created


@transaction.atomic
def run():
    providers = list(Provider.objects.order_by("pk")[:20])

    if not providers:
        raise RuntimeError("No hay providers para sembrar")

    created_skills = 0
    created_provider_services = 0
    created_skill_prices = 0
    created_extras = 0

    all_skill_objs = {}

    for code, skill_names in SERVICE_SKILL_MAP.items():
        service_type_obj = find_service_type_by_code(code)
        if not service_type_obj:
            print(f"[WARN] No encontré ServiceType para code={code}")
            continue

        all_skill_objs[service_type_obj.pk] = []

        for idx, skill_name in enumerate(skill_names, start=1):
            skill_obj, created = create_service_skill(service_type_obj, skill_name, idx)
            all_skill_objs[service_type_obj.pk].append(skill_obj)
            if created:
                created_skills += 1

    service_types = list(ServiceType.objects.all().order_by("pk")[:3])

    if not service_types:
        raise RuntimeError("No hay ServiceType para asociar a providers")

    for i, provider in enumerate(providers, start=1):
        for service_type_obj in service_types:
            skills = all_skill_objs.get(service_type_obj.pk, [])
            if not skills:
                continue

            chosen_skills = skills[:2] if len(skills) >= 2 else skills

            for skill_index, skill_obj in enumerate(chosen_skills, start=1):
                base_price = Decimal(str(random.choice([55, 65, 75, 85, 95, 110])))

                provider_service_obj, created = create_provider_service(
                    provider, service_type_obj, skill_obj, base_price
                )
                if created:
                    created_provider_services += 1

                psp_obj, psp_created = create_provider_skill_price(
                    provider, provider_service_obj, skill_obj, base_price
                )
                if psp_created:
                    created_skill_prices += 1

                skill_name = _skill_label(skill_obj)

                extras = EXTRAS_MAP.get(skill_name, [])[:2]
                for extra_order, (extra_name, extra_price) in enumerate(extras, start=1):
                    extra_obj, extra_created = create_provider_service_extra(
                        provider_service_obj,
                        extra_name,
                        extra_price,
                        extra_order,
                    )
                    if extra_created:
                        created_extras += 1

    print("==== SEED RESULT ====")
    print(f"Providers used: {len(providers)}")
    print(f"ServiceType total: {ServiceType.objects.count()}")
    print(f"ServiceSkill total: {ServiceSkill.objects.count()}")
    print(f"ProviderService total: {ProviderService.objects.count()}")
    if ProviderSkillPrice is not None:
        print(f"ProviderSkillPrice total: {ProviderSkillPrice.objects.count()}")
    if ProviderServiceExtra is not None:
        print(f"ProviderServiceExtra total: {ProviderServiceExtra.objects.count()}")
    print("--- created in this run ---")
    print(f"ServiceSkill created: {created_skills}")
    print(f"ProviderService created: {created_provider_services}")
    print(f"ProviderSkillPrice created: {created_skill_prices}")
    print(f"ProviderServiceExtra created: {created_extras}")


run()
