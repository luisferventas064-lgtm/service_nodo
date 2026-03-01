from django import forms

from .models import Client


class ClientRegisterForm(forms.Form):
    full_name = forms.CharField(max_length=255)
    email = forms.EmailField()
    phone_number = forms.CharField(max_length=20)


class ClientProfileForm(forms.ModelForm):
    class Meta:
        model = Client
        fields = [
            "country",
            "province",
            "city",
            "postal_code",
            "address_line1",
        ]
