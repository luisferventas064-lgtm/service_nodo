from django import forms
from django.utils.translation import gettext_lazy as _


class RoleLoginForm(forms.Form):
    identifier = forms.CharField(
        max_length=255,
        label=_("Email or phone"),
        widget=forms.TextInput(
            attrs={
                "autocomplete": "username",
            }
        ),
    )
    password = forms.CharField(
        label=_("Password"),
        widget=forms.PasswordInput(
            attrs={
                "autocomplete": "current-password",
            }
        ),
    )

    def apply_error_styles(self) -> None:
        non_field_error = bool(self.non_field_errors())

        for field_name, field in self.fields.items():
            has_error = bool(self.errors.get(field_name)) or non_field_error
            classes = ["form-input"]
            if has_error:
                classes.append("input-error")

            field.widget.attrs["class"] = " ".join(classes)
            if has_error:
                field.widget.attrs["aria-invalid"] = "true"
            else:
                field.widget.attrs.pop("aria-invalid", None)


class ForgotPasswordForm(forms.Form):
    phone = forms.CharField(
        max_length=20,
        label=_("Phone number"),
        widget=forms.TextInput(
            attrs={
                "type": "tel",
                "placeholder": _("+1 514 000 0000"),
            }
        ),
    )


class ResetPasswordConfirmForm(forms.Form):
    code = forms.CharField(
        max_length=6,
        label=_("Verification code"),
        widget=forms.TextInput(
            attrs={
                "inputmode": "numeric",
                "autocomplete": "one-time-code",
                "placeholder": _("6-digit code"),
            }
        ),
    )
    new_password = forms.CharField(
        label=_("New password"),
        widget=forms.PasswordInput(
            attrs={
                "autocomplete": "new-password",
                "placeholder": _("New password"),
            }
        )
    )
