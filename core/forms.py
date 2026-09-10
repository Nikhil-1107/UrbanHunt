from django import forms
from django.contrib.auth.forms import SetPasswordForm
from django.contrib.auth.forms import UserCreationForm

from .models import PlatformSettings, Property, User


NON_ADMIN_ROLE_CHOICES = [
    (User.Role.BUYER, "Buyer"),
    (User.Role.SELLER, "Seller"),
]


class RegisterForm(UserCreationForm):
    password2 = None
    mobile_no = forms.CharField(max_length=15, required=True)
    role = forms.ChoiceField(
        choices=[
            (User.Role.BUYER, "Buyer"),
            (User.Role.SELLER, "Seller"),
        ]
    )

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "email", "mobile_no", "role", "password1")

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data["email"]
        user.mobile_no = self.cleaned_data["mobile_no"]
        user.role = self.cleaned_data["role"]
        if commit:
            user.save()
        return user


class PropertyForm(forms.ModelForm):
	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		platform_settings = PlatformSettings.objects.first()
		if platform_settings:
			enabled_types = []
			if platform_settings.rent_enabled:
				enabled_types.append(Property.PropertyType.RENT)
			if platform_settings.sell_enabled:
				enabled_types.append(Property.PropertyType.SELL)
			self.fields["property_type"].choices = [
				(value, label) for value, label in Property.PropertyType.choices if value in enabled_types
			]
			if not self.instance.pk and platform_settings.default_property_status:
				self.instance.status = platform_settings.default_property_status
			if not platform_settings.allow_property_documents:
				self.fields["documents"].disabled = True
				self.fields["documents"].required = False

	class Meta:
		model = Property
		fields = (
			"title",
			"description",
			"price",
			"location",
			"property_type",
			"amenities",
			"image",
			"documents",
		)
		widgets = {
			"title": forms.TextInput(attrs={"placeholder": "e.g., Luxury Modern Villa with Private Pool", "class": "form-control"}),
			"description": forms.Textarea(attrs={"placeholder": "Describe the property features, amenities, and details...", "rows": 4, "class": "form-control"}),
			"price": forms.TextInput(attrs={"placeholder": "e.g., 1.3 cr, 25 lakh, 5k, 2500000", "class": "form-control"}),
			"location": forms.TextInput(attrs={"placeholder": "e.g., Mumbai, Bandra, Marine Drive", "class": "form-control"}),
			"property_type": forms.Select(attrs={"class": "form-control"}),
			"amenities": forms.Textarea(attrs={"placeholder": "e.g., Pool, Garden, Gym, Parking, Security, AC, WiFi...", "rows": 3, "class": "form-control"}),
			"image": forms.FileInput(attrs={"class": "form-control"}),
			"documents": forms.FileInput(attrs={"class": "form-control"}),
		}


class PlatformSettingsForm(forms.ModelForm):
	class Meta:
		model = PlatformSettings
		fields = (
			"site_name",
			"tagline",
			"contact_email",
			"contact_phone",
			"allow_user_registration",
			"rent_enabled",
			"sell_enabled",
			"allow_property_documents",
			"default_property_status",
		)
		widgets = {
			"site_name": forms.TextInput(attrs={"class": "settings-input"}),
			"tagline": forms.TextInput(attrs={"class": "settings-input"}),
			"contact_email": forms.EmailInput(attrs={"class": "settings-input"}),
			"contact_phone": forms.TextInput(attrs={"class": "settings-input"}),
			"default_property_status": forms.Select(attrs={"class": "settings-input"}),
		}

	def clean(self):
		cleaned_data = super().clean()
		if not cleaned_data.get("rent_enabled") and not cleaned_data.get("sell_enabled"):
			raise forms.ValidationError("At least one listing type must remain enabled.")
		return cleaned_data


class AdminPanelUserForm(forms.ModelForm):
    password1 = forms.CharField(required=False, widget=forms.PasswordInput())
    password2 = forms.CharField(required=False, widget=forms.PasswordInput())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["role"].choices = NON_ADMIN_ROLE_CHOICES

    def clean_role(self):
        role = self.cleaned_data["role"]
        if role not in {User.Role.BUYER, User.Role.SELLER}:
            raise forms.ValidationError("Only Buyer or Seller role can be set here.")
        return role

    def clean(self):
        cleaned_data = super().clean()
        password1 = cleaned_data.get("password1")
        password2 = cleaned_data.get("password2")

        if password1 or password2:
            if password1 != password2:
                raise forms.ValidationError("Password and confirm password must match.")
            if password1 and len(password1) < 8:
                raise forms.ValidationError("Password must be at least 8 characters long.")

        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=False)
        new_password = self.cleaned_data.get("password1")
        if new_password:
            user.set_password(new_password)
        if commit:
            user.save()
        return user

    class Meta:
        model = User
        fields = ("username", "email", "mobile_no", "role", "is_active")


class AdminPanelUserCreateForm(UserCreationForm):
    role = forms.ChoiceField(choices=NON_ADMIN_ROLE_CHOICES)
    email = forms.EmailField(required=True)
    mobile_no = forms.CharField(max_length=15, required=True)
    is_active = forms.BooleanField(required=False, initial=True)

    class Meta(UserCreationForm.Meta):
        model = User
        fields = (
            "username",
            "email",
            "mobile_no",
            "role",
            "is_active",
            "password1",
            "password2",
        )

    def clean_role(self):
        role = self.cleaned_data["role"]
        if role not in {User.Role.BUYER, User.Role.SELLER}:
            raise forms.ValidationError("Only Buyer or Seller role can be created here.")
        return role

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data["email"]
        user.mobile_no = self.cleaned_data["mobile_no"]
        user.role = self.cleaned_data["role"]
        user.is_active = self.cleaned_data["is_active"]
        if commit:
            user.save()
        return user


class AdminPanelPropertyForm(forms.ModelForm):
    seller = forms.ModelChoiceField(
        queryset=User.objects.filter(role=User.Role.SELLER).order_by("username")
    )

    class Meta:
        model = Property
        fields = (
            "title",
            "description",
            "price",
            "location",
            "property_type",
            "amenities",
            "image",
            "documents",
            "seller",
            "is_verified",
        )
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
            "amenities": forms.Textarea(attrs={"rows": 3}),
        }


class PasswordResetRequestForm(forms.Form):
    email = forms.EmailField()


class OTPVerificationForm(forms.Form):
    otp = forms.CharField(max_length=6, min_length=6)

    def clean_otp(self):
        otp = self.cleaned_data["otp"].strip()
        if not otp.isdigit():
            raise forms.ValidationError("OTP must contain only digits.")
        return otp


class PasswordResetNewPasswordForm(SetPasswordForm):
    pass


class UserProfileForm(forms.ModelForm):
    first_name = forms.CharField(
        max_length=150,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "First Name"}),
    )
    last_name = forms.CharField(
        max_length=150,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Last Name"}),
    )
    email = forms.EmailField(
        required=True,
        widget=forms.EmailInput(attrs={"class": "form-control", "placeholder": "name@example.com"}),
    )
    mobile_no = forms.CharField(
        max_length=15,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "+91 9876543210"}),
    )
    bio = forms.CharField(
        required=False,
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 3,
                "placeholder": "Tell us a bit about yourself...",
            }
        ),
    )
    avatar = forms.FileField(
        required=False,
        widget=forms.FileInput(attrs={"class": "form-control", "accept": "image/*"}),
    )

    class Meta:
        model = User
        fields = ("first_name", "last_name", "email", "mobile_no", "bio", "avatar")

    def save(self, commit=True):
        user = super().save(commit=False)
        avatar_file = self.cleaned_data.get("avatar")
        if not avatar_file:
            # Preserve existing avatar if no new file uploaded
            user.avatar = self.instance.avatar
        if commit:
            user.save()
        return user

