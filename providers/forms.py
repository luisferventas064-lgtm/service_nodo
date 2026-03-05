from django import forms

from core.utils.phone import PHONE_COUNTRY_CHOICES, PHONE_COUNTRY_NAMES, normalize_phone
from service_type.models import ServiceType

from .models import Provider, ProviderService


PROVIDER_ONBOARDING_TYPE_CHOICES = [
    ("individual", "Individual"),
    ("company", "Company"),
]

LANGUAGE_CHOICES = [
    ("English", "English"),
    ("French", "French"),
    ("Spanish", "Spanish"),
    ("Arabic", "Arabic"),
    ("Mandarin", "Mandarin"),
    ("Italian", "Italian"),
    ("Portuguese", "Portuguese"),
    ("Russian", "Russian"),
    ("Punjabi", "Punjabi"),
    ("Vietnamese", "Vietnamese"),
]


def _split_contact_name(full_name: str) -> tuple[str, str]:
    normalized = " ".join((full_name or "").strip().split())
    if not normalized:
        return "Pending", "Contact"

    parts = normalized.split(" ", 1)
    first_name = parts[0]
    last_name = parts[1] if len(parts) > 1 else "Contact"
    return first_name, last_name


class ProviderRegisterForm(forms.Form):
    business_name = forms.CharField(max_length=255)
    email = forms.EmailField()
    country = forms.ChoiceField(choices=PHONE_COUNTRY_CHOICES, initial="CA")
    phone_local = forms.CharField(max_length=20, label="Phone")
    languages_spoken = forms.MultipleChoiceField(
        choices=LANGUAGE_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        required=False,
    )
    password = forms.CharField(widget=forms.PasswordInput())
    confirm_password = forms.CharField(widget=forms.PasswordInput())
    provider_type = forms.ChoiceField(choices=PROVIDER_ONBOARDING_TYPE_CHOICES)

    def clean_email(self):
        email = self.cleaned_data["email"]
        if Provider.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("A provider with this email already exists.")
        return email

    def clean(self):
        cleaned_data = super().clean()
        country = cleaned_data.get("country")
        phone_local = cleaned_data.get("phone_local")
        if country and phone_local:
            try:
                phone_number = normalize_phone(country, phone_local)
            except ValueError as exc:
                self.add_error("phone_local", str(exc))
            else:
                if Provider.objects.filter(phone_number=phone_number).exists():
                    self.add_error("phone_local", "A provider with this phone number already exists.")
                else:
                    cleaned_data["phone_number"] = phone_number
                    cleaned_data["country_name"] = PHONE_COUNTRY_NAMES.get(country, "Canada")

        p1 = cleaned_data.get("password")
        p2 = cleaned_data.get("confirm_password")
        if p1 and p2 and p1 != p2:
            raise forms.ValidationError("Passwords do not match.")

        cleaned_data["languages_spoken"] = ", ".join(cleaned_data.get("languages_spoken", []))

        return cleaned_data


class ProviderBillingForm(forms.ModelForm):
    address_line1 = forms.CharField(max_length=255, label="Address Line")

    class Meta:
        model = Provider
        fields = [
            "address_line1",
            "city",
            "province",
            "postal_code",
        ]


class ProviderServiceForm(forms.ModelForm):
    class Meta:
        model = ProviderService
        fields = [
            "service_type",
            "custom_name",
            "billing_unit",
            "price_cents",
            "is_active",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["service_type"].queryset = ServiceType.objects.filter(
            is_active=True
        ).order_by("name")


class ProviderIndividualProfileForm(forms.ModelForm):
    languages_spoken = forms.MultipleChoiceField(
        choices=LANGUAGE_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        required=False,
    )
    accepts_terms = forms.BooleanField(required=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["service_area"].required = False
        existing_languages = (getattr(self.instance, "languages_spoken", "") or "").strip()
        if existing_languages:
            self.initial["languages_spoken"] = [
                language.strip()
                for language in existing_languages.split(",")
                if language.strip()
            ]

    def clean_languages_spoken(self):
        return ", ".join(self.cleaned_data.get("languages_spoken", []))

    class Meta:
        model = Provider
        fields = [
            "legal_name",
            "service_area",
            "languages_spoken",
        ]


class ProviderCompanyProfileForm(forms.ModelForm):
    contact_person_name = forms.CharField(max_length=255)
    languages_spoken = forms.MultipleChoiceField(
        choices=LANGUAGE_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        required=False,
    )
    accepts_terms = forms.BooleanField(required=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["company_name"].required = True
        self.fields["business_registration_number"].required = True
        self.fields["service_area"].required = False
        existing_languages = (getattr(self.instance, "languages_spoken", "") or "").strip()
        if existing_languages:
            self.initial["languages_spoken"] = [
                language.strip()
                for language in existing_languages.split(",")
                if language.strip()
            ]
        if self.instance and self.instance.pk:
            self.fields["contact_person_name"].initial = self.instance.contact_person_name

    def clean_languages_spoken(self):
        return ", ".join(self.cleaned_data.get("languages_spoken", []))

    class Meta:
        model = Provider
        fields = [
            "company_name",
            "business_registration_number",
            "service_area",
            "languages_spoken",
            "employee_count",
        ]

    def save(self, commit=True):
        provider = super().save(commit=False)
        first_name, last_name = _split_contact_name(
            self.cleaned_data["contact_person_name"]
        )
        provider.contact_first_name = first_name
        provider.contact_last_name = last_name
        if commit:
            provider.save()
        return provider
