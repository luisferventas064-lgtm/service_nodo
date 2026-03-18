from django import forms
from django.utils.translation import gettext_lazy as _

from core.utils.phone import (
    PHONE_COUNTRY_CHOICES,
    PHONE_COUNTRY_NAMES,
    is_phone_duplicate_allowed,
    normalize_phone,
)

from .models import Client


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


class ClientRegisterForm(forms.Form):
    full_name = forms.CharField(max_length=255)
    email = forms.EmailField()
    country = forms.ChoiceField(choices=PHONE_COUNTRY_CHOICES, initial="CA")
    phone_local = forms.CharField(max_length=20, label=_("Phone"))
    languages_spoken = forms.MultipleChoiceField(
        choices=LANGUAGE_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        required=False,
    )
    password = forms.CharField(widget=forms.PasswordInput())
    confirm_password = forms.CharField(widget=forms.PasswordInput())

    def clean_email(self):
        email = self.cleaned_data["email"]
        if Client.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError(_("A client with this email already exists."))
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
                    Client.objects.filter(phone_number=phone_number).exists()
                    and not is_phone_duplicate_allowed(phone_number)
                ):
                    self.add_error("phone_local", _("A client with this phone number already exists."))
                else:
                    cleaned_data["phone_number"] = phone_number
                    cleaned_data["country_name"] = PHONE_COUNTRY_NAMES.get(country, "Canada")

        p1 = cleaned_data.get("password")
        p2 = cleaned_data.get("confirm_password")
        if p1 and p2 and p1 != p2:
            raise forms.ValidationError(_("Passwords do not match."))

        cleaned_data["languages_spoken"] = ", ".join(cleaned_data.get("languages_spoken", []))

        return cleaned_data


class ClientProfileForm(forms.ModelForm):
    languages_spoken = forms.MultipleChoiceField(
        choices=LANGUAGE_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        required=False,
    )
    accepts_terms = forms.BooleanField(required=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
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
        model = Client
        fields = [
            "country",
            "province",
            "city",
            "postal_code",
            "address_line1",
            "languages_spoken",
        ]
