from django import forms
from django.utils.translation import gettext_lazy as _

from core.utils.phone import (
    PHONE_COUNTRY_CHOICES,
    PHONE_COUNTRY_NAMES,
    is_phone_duplicate_allowed,
    normalize_phone,
)
from service_type.models import ServiceType

from .models import (
    Provider,
    ProviderBillingProfile,
    ProviderCertificate,
    ProviderInsurance,
    ProviderService,
)


PROVIDER_ONBOARDING_TYPE_CHOICES = [
    ("individual", _("Individual")),
    ("company", _("Company")),
]

LANGUAGE_CHOICES = [
    ("English", _("English")),
    ("French", _("French")),
    ("Spanish", _("Spanish")),
    ("Arabic", _("Arabic")),
    ("Mandarin", _("Mandarin")),
    ("Italian", _("Italian")),
    ("Portuguese", _("Portuguese")),
    ("Russian", _("Russian")),
    ("Punjabi", _("Punjabi")),
    ("Vietnamese", _("Vietnamese")),
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
    business_name = forms.CharField(max_length=255, label=_("Business name"))
    email = forms.EmailField(label=_("Email"))
    country = forms.ChoiceField(
        choices=PHONE_COUNTRY_CHOICES,
        initial="CA",
        label=_("Country"),
    )
    phone_local = forms.CharField(max_length=20, label=_("Phone"))
    languages_spoken = forms.MultipleChoiceField(
        choices=LANGUAGE_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        required=False,
        label=_("Languages spoken"),
    )
    password = forms.CharField(
        widget=forms.PasswordInput(),
        label=_("Password"),
    )
    confirm_password = forms.CharField(
        widget=forms.PasswordInput(),
        label=_("Confirm password"),
    )
    provider_type = forms.ChoiceField(
        choices=PROVIDER_ONBOARDING_TYPE_CHOICES,
        label=_("Provider type"),
    )

    def clean_email(self):
        email = self.cleaned_data["email"]
        if Provider.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError(_("A provider with this email already exists."))
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
                if (
                    Provider.objects.filter(phone_number=phone_number).exists()
                    and not is_phone_duplicate_allowed(phone_number)
                ):
                    self.add_error(
                        "phone_local",
                        _("A provider with this phone number already exists."),
                    )
                else:
                    cleaned_data["phone_number"] = phone_number
                    cleaned_data["country_name"] = PHONE_COUNTRY_NAMES.get(
                        country,
                        _("Canada"),
                    )

        p1 = cleaned_data.get("password")
        p2 = cleaned_data.get("confirm_password")
        if p1 and p2 and p1 != p2:
            raise forms.ValidationError(_("Passwords do not match."))

        cleaned_data["languages_spoken"] = ", ".join(cleaned_data.get("languages_spoken", []))

        return cleaned_data


class ProviderBillingForm(forms.ModelForm):
    address_line1 = forms.CharField(max_length=255, label=_("Address Line"))
    entity_type = forms.ChoiceField(
        choices=ProviderBillingProfile._meta.get_field("entity_type").choices,
        required=True,
        label=_("Entity Type"),
    )
    legal_name = forms.CharField(max_length=255, required=True, label=_("Legal Name"))
    business_name = forms.CharField(max_length=255, required=False, label=_("Business Name"))
    gst_hst_number = forms.CharField(max_length=64, required=False, label=_("GST/HST Number"))
    qst_tvq_number = forms.CharField(max_length=64, required=False, label=_("QST/TVQ Number"))
    neq_number = forms.CharField(max_length=64, required=False, label=_("NEQ Number"))
    bn_number = forms.CharField(max_length=64, required=False, label=_("BN Number"))

    class Meta:
        model = Provider
        fields = [
            "address_line1",
            "city",
            "province",
            "postal_code",
        ]

    def __init__(self, *args, provider=None, **kwargs):
        self.provider = provider or kwargs.get("instance")
        super().__init__(*args, **kwargs)

        billing_profile = getattr(self.provider, "billing_profile", None) if self.provider else None
        if billing_profile:
            self.fields["entity_type"].initial = billing_profile.entity_type
            self.fields["legal_name"].initial = billing_profile.legal_name
            self.fields["business_name"].initial = billing_profile.business_name
            self.fields["gst_hst_number"].initial = billing_profile.gst_hst_number
            self.fields["qst_tvq_number"].initial = billing_profile.qst_tvq_number
            self.fields["neq_number"].initial = billing_profile.neq_number
            self.fields["bn_number"].initial = billing_profile.bn_number

    def save(self, commit=True):
        provider = super().save(commit=commit)

        entity_type = (self.cleaned_data.get("entity_type") or "").strip()
        billing_profile, _ = ProviderBillingProfile.objects.get_or_create(
            provider=provider,
            defaults={"entity_type": entity_type},
        )
        billing_profile.entity_type = entity_type
        billing_profile.legal_name = (self.cleaned_data.get("legal_name") or "").strip()
        billing_profile.business_name = (self.cleaned_data.get("business_name") or "").strip()
        billing_profile.gst_hst_number = (self.cleaned_data.get("gst_hst_number") or "").strip()
        billing_profile.qst_tvq_number = (self.cleaned_data.get("qst_tvq_number") or "").strip()
        billing_profile.neq_number = (self.cleaned_data.get("neq_number") or "").strip()
        billing_profile.bn_number = (self.cleaned_data.get("bn_number") or "").strip()

        if commit:
            billing_profile.save()

        self.billing_profile = billing_profile
        return provider

    def is_billing_complete(self):
        required_values = [
            (self.cleaned_data.get("address_line1") or "").strip(),
            (self.cleaned_data.get("city") or "").strip(),
            (self.cleaned_data.get("province") or "").strip(),
            (self.cleaned_data.get("postal_code") or "").strip(),
            (self.cleaned_data.get("entity_type") or "").strip(),
            (self.cleaned_data.get("legal_name") or "").strip(),
        ]
        return all(required_values)


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
        label=_("Languages spoken"),
    )
    accepts_terms = forms.BooleanField(required=True, label=_("Accept terms"))

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
    contact_person_name = forms.CharField(max_length=255, label=_("Contact person name"))
    languages_spoken = forms.MultipleChoiceField(
        choices=LANGUAGE_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        required=False,
        label=_("Languages spoken"),
    )
    accepts_terms = forms.BooleanField(required=True, label=_("Accept terms"))

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


class ProviderInsuranceForm(forms.ModelForm):
    class Meta:
        model = ProviderInsurance
        fields = [
            "has_insurance",
            "insurance_company",
            "policy_number",
            "coverage_amount",
            "expiry_date",
        ]
        widgets = {
            "has_insurance": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "insurance_company": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": _("Insurance company"),
                }
            ),
            "policy_number": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": _("Policy number"),
                }
            ),
            "coverage_amount": forms.NumberInput(
                attrs={
                    "class": "form-control",
                    "placeholder": _("Coverage amount"),
                    "step": "0.01",
                    "min": "0",
                }
            ),
            "expiry_date": forms.DateInput(
                attrs={
                    "class": "form-control",
                    "type": "date",
                }
            ),
        }


class ProviderCertificateForm(forms.ModelForm):
    class Meta:
        model = ProviderCertificate
        fields = [
            "cert_type",
            "cert_name",
            "taken_at",
            "issued_by",
            "issued_country",
            "issued_city",
            "issued_date",
            "expires_date",
            "notes",
        ]
        widgets = {
            "cert_type": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": _("Certificate type (example: RBQ)"),
                }
            ),
            "cert_name": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": _("Certificate name"),
                }
            ),
            "taken_at": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": _("Taken at / reference"),
                }
            ),
            "issued_by": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": _("Issued by"),
                }
            ),
            "issued_country": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": _("Issued country"),
                }
            ),
            "issued_city": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": _("Issued city"),
                }
            ),
            "issued_date": forms.DateInput(
                attrs={
                    "class": "form-control",
                    "type": "date",
                }
            ),
            "expires_date": forms.DateInput(
                attrs={
                    "class": "form-control",
                    "type": "date",
                }
            ),
            "notes": forms.Textarea(
                attrs={
                    "class": "form-control",
                    "rows": 3,
                    "placeholder": _("Notes"),
                }
            ),
        }
