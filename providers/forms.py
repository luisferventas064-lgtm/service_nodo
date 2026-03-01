from django import forms

from .models import Provider, ProviderService, ServiceCategory


PROVIDER_ONBOARDING_TYPE_CHOICES = [
    ("individual", "Individual"),
    ("company", "Company"),
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
    phone_number = forms.CharField(max_length=20)
    provider_type = forms.ChoiceField(choices=PROVIDER_ONBOARDING_TYPE_CHOICES)


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
            "category",
            "custom_name",
            "billing_unit",
            "price_cents",
            "is_active",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].queryset = ServiceCategory.objects.filter(
            is_active=True
        ).order_by("name")


class ProviderIndividualProfileForm(forms.ModelForm):
    accepts_terms = forms.BooleanField(required=True)

    class Meta:
        model = Provider
        fields = [
            "legal_name",
            "service_area",
        ]


class ProviderCompanyProfileForm(forms.ModelForm):
    contact_person_name = forms.CharField(max_length=255)
    accepts_terms = forms.BooleanField(required=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["business_registration_number"].required = True
        self.fields["service_area"].required = True
        if self.instance and self.instance.pk:
            self.fields["contact_person_name"].initial = self.instance.contact_person_name

    class Meta:
        model = Provider
        fields = [
            "business_registration_number",
            "service_area",
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
