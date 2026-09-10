from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone


class User(AbstractUser):
	class Role(models.TextChoices):
		BUYER = "buyer", "Buyer"
		SELLER = "seller", "Seller"
		ADMIN = "admin", "Admin"

	role = models.CharField(max_length=10, choices=Role.choices, default=Role.BUYER)
	mobile_no = models.CharField(
		max_length=15,
		blank=True,
		validators=[RegexValidator(regex=r"^\+?[0-9]{10,15}$", message="Enter a valid mobile number.")],
	)
	avatar = models.FileField(upload_to="avatars/", blank=True, null=True)
	bio = models.TextField(blank=True, max_length=500)

	def save(self, *args, **kwargs):
		if self.is_superuser:
			self.role = self.Role.ADMIN
		return super().save(*args, **kwargs)

	def __str__(self):
		return f"{self.username} ({self.role})"


class Property(models.Model):
	class PropertyType(models.TextChoices):
		RENT = "rent", "Rent"
		SELL = "sell", "Sell"

	class PropertyStatus(models.TextChoices):
		AVAILABLE = "available", "Available"
		SOLD = "sold", "Sold"
		RENTED = "rented", "Rented"

	title = models.CharField(max_length=255)
	description = models.TextField()
	price = models.CharField(
		max_length=50,
		help_text="e.g., 1.3 cr, 25 lakh, 5k, ₹2500000"
	)
	location = models.CharField(max_length=255)
	property_type = models.CharField(max_length=10, choices=PropertyType.choices)
	amenities = models.TextField(blank=True)
	image = models.FileField(upload_to="properties/images/")
	documents = models.FileField(upload_to="properties/documents/", blank=True, null=True)
	seller = models.ForeignKey(
		User,
		on_delete=models.CASCADE,
		related_name="properties",
		limit_choices_to={"role": User.Role.SELLER},
	)
	is_verified = models.BooleanField(default=False)
	status = models.CharField(
		max_length=10,
		choices=PropertyStatus.choices,
		default=PropertyStatus.AVAILABLE,
	)
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	def clean(self):
		platform_settings = PlatformSettings.objects.first()
		documents_allowed = not platform_settings or platform_settings.allow_property_documents
		if documents_allowed and self.property_type == self.PropertyType.SELL and not self.documents:
			raise ValidationError({"documents": "Documents are required for properties for sale."})

	def save(self, *args, **kwargs):
		self.full_clean()
		return super().save(*args, **kwargs)

	def __str__(self):
		return self.title


class InquiryChat(models.Model):
	buyer = models.ForeignKey(
		User,
		on_delete=models.CASCADE,
		related_name="inquiries",
		limit_choices_to={"role": User.Role.BUYER},
	)
	property = models.ForeignKey(Property, on_delete=models.CASCADE, related_name="inquiries")
	message = models.TextField()
	created_at = models.DateTimeField(auto_now_add=True)

	class Meta:
		ordering = ["-created_at"]

	def __str__(self):
		return f"Inquiry by {self.buyer.username} for {self.property.title}"


class SavedProperty(models.Model):
	buyer = models.ForeignKey(
		User,
		on_delete=models.CASCADE,
		related_name="saved_properties",
		limit_choices_to={"role": User.Role.BUYER},
	)
	property = models.ForeignKey(Property, on_delete=models.CASCADE, related_name="saved_by")
	created_at = models.DateTimeField(auto_now_add=True)

	class Meta:
		unique_together = ("buyer", "property")
		ordering = ["-created_at"]

	def clean(self):
		if self.buyer and self.buyer.role != User.Role.BUYER:
			raise ValidationError({"buyer": "Only buyers can save properties."})

	def save(self, *args, **kwargs):
		self.full_clean()
		return super().save(*args, **kwargs)

	def __str__(self):
		return f"{self.buyer.username} saved {self.property.title}"


class ChatMessage(models.Model):
	inquiry = models.ForeignKey(InquiryChat, on_delete=models.CASCADE, related_name="messages")
	sender = models.ForeignKey(User, on_delete=models.CASCADE, related_name="chat_messages")
	message = models.TextField()
	created_at = models.DateTimeField(auto_now_add=True)
	read_at = models.DateTimeField(blank=True, null=True)

	class Meta:
		ordering = ["created_at"]

	def __str__(self):
		return f"{self.sender.username}: {self.message[:30]}"


class PasswordResetOTP(models.Model):
	user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="password_reset_otps")
	otp_hash = models.CharField(max_length=255)
	expires_at = models.DateTimeField()
	is_used = models.BooleanField(default=False)
	created_at = models.DateTimeField(auto_now_add=True)

	class Meta:
		ordering = ["-created_at"]

	def is_expired(self):
		return timezone.now() >= self.expires_at

	def __str__(self):
		return f"Password reset OTP for {self.user.username}"





class ContactMessage(models.Model):
	full_name = models.CharField(max_length=150)
	email = models.EmailField()
	subject = models.CharField(max_length=255)
	message = models.TextField()
	created_at = models.DateTimeField(auto_now_add=True)

	class Meta:
		ordering = ["-created_at"]

	def __str__(self):
		return f"Contact message from {self.full_name} ({self.subject})"


class PopularCity(models.Model):
	name = models.CharField(max_length=100)
	state = models.CharField(max_length=100)
	image = models.ImageField(upload_to="cities/", blank=True, null=True)
	order = models.PositiveIntegerField(default=0)

	class Meta:
		verbose_name = "Popular City"
		verbose_name_plural = "Popular Cities"
		ordering = ["order", "name"]

	def __str__(self):
		return f"{self.name}, {self.state}"


class PlatformSettings(models.Model):
	"""The editable, application-wide settings managed from the admin panel."""
	site_name = models.CharField(max_length=120, default="Urban Hunt")
	tagline = models.CharField(max_length=255, default="Better Homes, Brighter Tomorrows")
	contact_email = models.EmailField(default="rathodnikhil1107@gmail.com")
	contact_phone = models.CharField(max_length=40, blank=True)
	allow_user_registration = models.BooleanField(default=True)
	rent_enabled = models.BooleanField(default=True)
	sell_enabled = models.BooleanField(default=True)
	allow_property_documents = models.BooleanField(default=True)
	default_property_status = models.CharField(
		max_length=10,
		choices=Property.PropertyStatus.choices,
		default=Property.PropertyStatus.AVAILABLE,
	)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		verbose_name = "Platform Settings"
		verbose_name_plural = "Platform Settings"

	def __str__(self):
		return self.site_name

