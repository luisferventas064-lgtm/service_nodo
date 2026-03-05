from django import forms


class RoleLoginForm(forms.Form):
    identifier = forms.CharField(
        max_length=255,
        label="Email or phone",
    )
    password = forms.CharField(
        widget=forms.PasswordInput(),
    )


class ForgotPasswordForm(forms.Form):
    phone = forms.CharField(
        max_length=20,
        label="Phone number",
        widget=forms.TextInput(
            attrs={
                "type": "tel",
                "placeholder": "+1 514 000 0000",
            }
        ),
    )


class ResetPasswordConfirmForm(forms.Form):
    code = forms.CharField(
        max_length=6,
        widget=forms.TextInput(
            attrs={
                "inputmode": "numeric",
                "autocomplete": "one-time-code",
                "placeholder": "6-digit code",
            }
        ),
    )
    new_password = forms.CharField(
        widget=forms.PasswordInput(
            attrs={
                "autocomplete": "new-password",
                "placeholder": "New password",
            }
        )
    )
