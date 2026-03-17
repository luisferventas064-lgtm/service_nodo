from decimal import Decimal, ROUND_HALF_UP
import re

from django import forms
from django.utils.translation import gettext_lazy as _

from providers.models import ProviderService

ADDON_PREFIX = "ADDON: "

SERVICE_PRESETS_BY_TYPE_NAME = {
    "Carpentry (doors, trim, minor builds)": [
        "Door repair",
        "Door installation",
        "Trim / molding repair",
        "Trim / molding installation",
        "Shelving / minor build",
        "Cabinet adjustment",
        "Hardware replacement",
    ],
    "Drain Cleaning / Unclogging": [
        "Sink unclogging",
        "Toilet unclogging",
        "Shower / tub unclogging",
        "Floor drain cleaning",
        "Main drain cleaning",
        "Camera inspection",
        "Emergency visit",
    ],
    "Drywall Repair (patching, cracks)": [
        "Small patch",
        "Medium patch",
        "Large patch",
        "Crack repair",
        "Hole repair",
        "Water-damage patch",
        "Sanding / finishing",
    ],
    "Electrical Services (outlets, lighting, panel issues)": [
        "Outlet repair",
        "Outlet installation",
        "Light fixture installation",
        "Light fixture repair",
        "Switch repair / install",
        "Breaker / panel issue",
        "Troubleshooting",
        "Emergency visit",
    ],
    "Gutter Cleaning / Repair": [
        "Gutter cleaning",
        "Downspout cleaning",
        "Gutter repair",
        "Downspout repair",
        "Gutter guard installation",
        "Leak sealing",
        "Seasonal maintenance",
    ],
    "Handyman Services (general home repairs)": [
        "Minor home repair",
        "Fixture installation",
        "Wall mounting",
        "Furniture assembly",
        "Caulking / sealing",
        "Hardware replacement",
        "General maintenance",
    ],
    "House Cleaning (regular / deep cleaning)": [
        "Standard cleaning",
        "Deep cleaning",
        "Post-renovation cleaning",
        "Kitchen deep cleaning",
        "Bathroom deep cleaning",
        "One-time cleaning",
        "Recurring cleaning",
    ],
    "HVAC Services (heating & air conditioning)": [
        "Furnace repair",
        "Furnace maintenance",
        "AC repair",
        "AC maintenance",
        "Thermostat installation",
        "Duct inspection",
        "Filter replacement",
        "Emergency visit",
    ],
    "Interior / Exterior Painting": [
        "Interior painting",
        "Exterior painting",
        "Touch-up painting",
        "Wall painting",
        "Ceiling painting",
        "Trim painting",
        "Surface prep",
        "Staining",
    ],
    "Junk Removal / Hauling": [
        "Small load removal",
        "Full load removal",
        "Furniture removal",
        "Appliance removal",
        "Yard waste removal",
        "Construction debris removal",
        "Garage / basement cleanout",
    ],
    "Landscaping / Lawn Care": [
        "Lawn mowing",
        "Yard cleanup",
        "Hedge trimming",
        "Garden maintenance",
        "Mulching",
        "Weed control",
        "Seasonal cleanup",
        "Sod installation",
    ],
    "Locksmith Services (locks, rekeying, emergencies)": [
        "Lockout service",
        "Lock repair",
        "Lock installation",
        "Rekey service",
        "Deadbolt installation",
        "Key replacement",
        "Smart lock installation",
        "Emergency visit",
    ],
    "Move-in / Move-out Cleaning": [
        "Move-in cleaning",
        "Move-out cleaning",
        "Empty unit deep cleaning",
        "Appliance cleaning",
        "Cabinet / drawer cleaning",
        "Wall spot cleaning",
        "Pre-listing cleaning",
    ],
    "Pest Control": [
        "Inspection",
        "Preventive treatment",
        "Ant treatment",
        "Roach treatment",
        "Rodent control",
        "Bed bug treatment",
        "Wasp / hornet treatment",
        "Follow-up visit",
    ],
    "Plumbing (leaks, faucets, toilets)": [
        "Leak repair",
        "Faucet repair",
        "Faucet installation",
        "Toilet repair",
        "Toilet installation",
        "Pipe repair",
        "Plumbing inspection",
        "Emergency visit",
    ],
    "Roofing Repair / Replacement": [
        "Roof inspection",
        "Leak repair",
        "Shingle repair",
        "Shingle replacement",
        "Flashing repair",
        "Partial roof replacement",
        "Full roof replacement",
        "Emergency tarp service",
    ],
    "Siding Repair / Installation": [
        "Siding repair",
        "Siding replacement",
        "New siding installation",
        "Panel replacement",
        "Trim / fascia repair",
        "Weatherproof sealing",
        "Inspection",
    ],
    "Snow Removal": [
        "Driveway snow removal",
        "Walkway snow removal",
        "Stairs snow removal",
        "Roof snow removal",
        "De-icing",
        "Salting / sanding",
        "Seasonal contract",
        "Emergency snow service",
    ],
    "Water Heater Installation / Repair": [
        "Water heater repair",
        "Water heater installation",
        "Water heater replacement",
        "Tankless water heater service",
        "Thermostat repair",
        "Leak inspection",
        "Emergency visit",
    ],
    "Window & Door Repair / Installation": [
        "Window repair",
        "Window installation",
        "Door repair",
        "Door installation",
        "Screen repair",
        "Weatherstripping replacement",
        "Glass replacement",
        "Hardware adjustment",
    ],
}


def _normalize_name(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s


class ProviderServiceCreateForm(forms.ModelForm):
    # UI fields (not DB)
    preset = forms.ChoiceField(
        choices=[],
        required=False,
        label=_("Service option"),
    )
    is_addon = forms.BooleanField(
        required=False,
        initial=False,
        label=_("This is an add-on (extra)"),
    )

    # Keep custom_name editable (DB field), but not required because preset can fill it.
    custom_name = forms.CharField(
        max_length=150,
        required=False,
        label=_("Service name"),
    )

    # Dollars in UI, cents in DB
    price = forms.DecimalField(
        max_digits=10,
        decimal_places=2,
        min_value=Decimal("0.00"),
        required=True,
        label=_("Price (CAD)"),
        help_text=_("Enter the regular price in CAD (e.g., 120.00)."),
    )

    class Meta:
        model = ProviderService
        fields = ["preset", "is_addon", "custom_name", "description", "billing_unit", "price"]

    def __init__(self, *args, service_type_name: str | None = None, **kwargs):
        super().__init__(*args, **kwargs)

        options = SERVICE_PRESETS_BY_TYPE_NAME.get(service_type_name, [])
        # Always offer Other (type)
        preset_choices = [("", _("Select an option"))] + [
            (o, _(o)) for o in options
        ] + [("OTHER", _("Other (type)"))]
        self.fields["preset"].choices = preset_choices

        self.fields["custom_name"].label = _("Service name")
        self.fields["custom_name"].widget.attrs.update(
            {"placeholder": _("Type a name (or choose an option above)")}
        )
        self.fields["description"].label = _("Description")
        self.fields["billing_unit"].label = _("Billing unit")
        self.fields["description"].widget.attrs.update({"rows": 4})

        # If editing an existing ADDON, pre-check the box and show name without prefix in the input
        if self.instance and getattr(self.instance, "pk", None):
            current = (getattr(self.instance, "custom_name", "") or "").strip()
            if current.startswith(ADDON_PREFIX):
                self.initial["is_addon"] = True
                self.initial["custom_name"] = current[len(ADDON_PREFIX):].strip()

    def clean(self):
        cleaned = super().clean()

        preset = (cleaned.get("preset") or "").strip()
        is_addon = bool(cleaned.get("is_addon"))
        name = (cleaned.get("custom_name") or "").strip()

        # If preset selected (and not OTHER), use it as the base name unless user typed a name.
        if preset and preset not in ("OTHER",) and not name:
            name = str(dict(self.fields["preset"].choices).get(preset, preset))

        if preset == "OTHER" and not name:
            self.add_error("custom_name", _("Please enter a custom service name."))
            return cleaned

        if not preset and not name:
            self.add_error(
                "custom_name",
                _("Please select an option or enter a service name."),
            )
            return cleaned

        # Apply ADDON prefix if needed
        final_name = f"{ADDON_PREFIX}{name}" if is_addon else name
        cleaned["custom_name"] = final_name
        cleaned["_normalized_custom_name"] = _normalize_name(final_name)

        return cleaned

    def clean_price(self):
        value = self.cleaned_data["price"]
        cents = (value * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        return int(cents)

    def save(self, commit=True):
        obj = super().save(commit=False)
        obj.price_cents = self.cleaned_data["price"]
        obj.custom_name = self.cleaned_data["custom_name"]
        if commit:
            obj.save()
        return obj
