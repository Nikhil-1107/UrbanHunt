from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.utils.html import format_html

from .models import ChatMessage, InquiryChat, PopularCity, Property, SavedProperty, User

admin.site.site_header = "Urban Hunt Admin"
admin.site.site_title = "Urban Hunt Admin"
admin.site.index_title = "Control Panel"


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
	list_display = ("username", "email", "role", "is_active")
	list_filter = ("role", "is_superuser", "is_active")
	search_fields = ("username", "email")

	fieldsets = DjangoUserAdmin.fieldsets + (
		("Urban Hunt", {"fields": ("role",)}),
	)
	add_fieldsets = DjangoUserAdmin.add_fieldsets + (
		("Urban Hunt", {"fields": ("role",)}),
	)


@admin.register(Property)
class PropertyAdmin(admin.ModelAdmin):
	list_display = (
		"title",
		"seller",
		"property_type",
		"price",
		"is_verified",
		"documents_uploaded",
		"document_link",
		"created_at",
	)
	list_filter = ("is_verified", "property_type", "created_at")
	search_fields = ("title", "location", "seller__username")
	autocomplete_fields = ("seller",)
	actions = (
		"approve_selected_listings",
		"unapprove_selected_listings",
		"verify_documents_for_selected",
		"clear_document_verification_for_selected",
	)

	@admin.display(boolean=True, description="Docs Uploaded")
	def documents_uploaded(self, obj):
		return bool(obj.documents)

	@admin.display(description="Document")
	def document_link(self, obj):
		if not obj.documents:
			return "No document"
		return format_html('<a href="{}" target="_blank" rel="noopener">Open</a>', obj.documents.url)

	@admin.action(description="Approve selected listings")
	def approve_selected_listings(self, request, queryset):
		updated = queryset.update(is_verified=True)
		self.message_user(request, f"Approved {updated} listing(s).")

	@admin.action(description="Unapprove selected listings")
	def unapprove_selected_listings(self, request, queryset):
		updated = queryset.update(is_verified=False)
		self.message_user(request, f"Unapproved {updated} listing(s).")

	@admin.action(description="Mark selected listings as document-verified")
	def verify_documents_for_selected(self, request, queryset):
		with_docs = queryset.exclude(documents="")
		updated = with_docs.update(is_verified=True)
		self.message_user(
			request,
			f"Document-verified and approved {updated} listing(s) with uploaded documents.",
		)

	@admin.action(description="Clear document verification for selected")
	def clear_document_verification_for_selected(self, request, queryset):
		updated = queryset.update(is_verified=False)
		self.message_user(request, f"Cleared verification for {updated} listing(s).")


@admin.register(InquiryChat)
class InquiryChatAdmin(admin.ModelAdmin):
	list_display = ("id", "buyer", "property", "created_at")
	list_filter = ("created_at",)
	search_fields = ("buyer__username", "property__title", "message")


@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
	list_display = ("id", "inquiry", "sender", "created_at", "read_at")
	list_filter = ("created_at", "read_at")
	search_fields = ("sender__username", "message")


@admin.register(SavedProperty)
class SavedPropertyAdmin(admin.ModelAdmin):
	list_display = ("id", "buyer", "property", "created_at")
	list_filter = ("created_at",)
	search_fields = ("buyer__username", "property__title")





@admin.register(PopularCity)
class PopularCityAdmin(admin.ModelAdmin):
	list_display = ("name", "state", "order")
	list_editable = ("order",)
	search_fields = ("name", "state")

