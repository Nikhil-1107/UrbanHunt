from .models import ChatMessage, InquiryChat, PlatformSettings, User


def platform_settings(request):
	return {"platform_settings": PlatformSettings.objects.first()}


def chat_notifications(request):
	user = request.user
	if not user.is_authenticated or user.is_superuser or user.role == User.Role.ADMIN:
		return {"chat_notifications": {"unread_count": 0, "items": []}}

	if user.role == User.Role.BUYER:
		inquiries = InquiryChat.objects.filter(buyer=user)
	elif user.role == User.Role.SELLER:
		inquiries = InquiryChat.objects.filter(property__seller=user)
	else:
		return {"chat_notifications": {"unread_count": 0, "items": []}}

	unread_messages = (
		ChatMessage.objects.filter(inquiry__in=inquiries, read_at__isnull=True)
		.exclude(sender=user)
		.select_related("sender", "inquiry", "inquiry__property")
		.order_by("-created_at")
	)

	items = []
	for message in unread_messages[:5]:
		items.append(
			{
				"inquiry_id": message.inquiry_id,
				"sender_name": message.sender.username,
				"property_title": message.inquiry.property.title,
				"preview": message.message,
			}
		)

	return {
		"chat_notifications": {
			"unread_count": unread_messages.count(),
			"items": items,
		}
	}



