from django import forms

from core.utils.phone import PHONE_COUNTRY_CHOICES, PHONE_COUNTRY_NAMES, normalize_phone

from .models import Worker

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


class WorkerRegisterForm(forms.Form):
    full_name = forms.CharField(max_length=255)
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

    def clean_email(self):
        email = self.cleaned_data["email"]
        if Worker.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("A worker with this email already exists.")
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
                if Worker.objects.filter(phone_number=phone_number).exists():
                    self.add_error("phone_local", "A worker with this phone number already exists.")
                else:
                    cleaned_data["phone_number"] = phone_number
                    cleaned_data["country_name"] = PHONE_COUNTRY_NAMES.get(country, "Canada")

        p1 = cleaned_data.get("password")
        p2 = cleaned_data.get("confirm_password")
        if p1 and p2 and p1 != p2:
            raise forms.ValidationError("Passwords do not match.")

        cleaned_data["languages_spoken"] = ", ".join(cleaned_data.get("languages_spoken", []))

        return cleaned_data


class WorkerProfileForm(forms.ModelForm):
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
        model = Worker
        fields = [
            "first_name",
            "last_name",
            "languages_spoken",
        ]
