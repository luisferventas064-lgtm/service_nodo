from decimal import Decimal, ROUND_HALF_UP

from django import forms

from providers.models import ProviderService


class ProviderServiceCreateForm(forms.ModelForm):
    # Provider enters dollars; DB stores cents
    price = forms.DecimalField(
        max_digits=10,
        decimal_places=2,
        min_value=Decimal("0.00"),
        required=True,
        help_text="Enter the regular price in CAD (e.g., 120.00).",
    )

    class Meta:
        model = ProviderService
        fields = ["custom_name", "description", "billing_unit", "price"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["custom_name"].widget.attrs.update({"placeholder": "e.g., Deep Cleaning"})
        self.fields["description"].widget.attrs.update({"rows": 4})

    def clean_price(self):
        value = self.cleaned_data["price"]
        cents = (value * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        return int(cents)

    def save(self, commit=True):
        obj = super().save(commit=False)
        obj.price_cents = self.cleaned_data["price"]
        if commit:
            obj.save()
        return obj
