import shutil
import re
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from .context_processors import chat_notifications
from .models import ChatMessage, InquiryChat, PasswordResetOTP, Property, SavedProperty, User
from .views import get_seller_dashboard_context


TEST_MEDIA_ROOT = Path(__file__).resolve().parent.parent / "test_media"


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class CoreFlowTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEST_MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        self.factory = RequestFactory()
        self.password = "test-pass-123"
        self.buyer = User.objects.create_user(
            username="buyer",
            password=self.password,
            role=User.Role.BUYER,
        )
        self.seller = User.objects.create_user(
            username="seller",
            password=self.password,
            role=User.Role.SELLER,
        )
        self.other_buyer = User.objects.create_user(
            username="other-buyer",
            password=self.password,
            role=User.Role.BUYER,
        )

    def make_property(self, *, seller, title="Harbor Loft", is_verified=True, property_type=None):
        property_type = property_type or Property.PropertyType.RENT
        payload = {
            "title": title,
            "description": "Sunlit rooms with quick access to the city center.",
            "price": "250000.00",
            "location": "Kolkata",
            "property_type": property_type,
            "amenities": "Pool, Gym",
            "image": SimpleUploadedFile(
                f"{title.lower().replace(' ', '-')}.jpg",
                b"fake-image-content",
                content_type="image/jpeg",
            ),
            "seller": seller,
            "is_verified": is_verified,
        }
        if property_type == Property.PropertyType.SELL:
            payload["documents"] = SimpleUploadedFile(
                f"{title.lower().replace(' ', '-')}.pdf",
                b"fake-pdf-content",
                content_type="application/pdf",
            )
        return Property.objects.create(**payload)

    def build_request(self, user):
        request = self.factory.get("/")
        request.user = user
        return request

    def test_sale_property_requires_documents(self):
        listing = Property(
            title="Document Check",
            description="Sale listing without documents.",
            price="900000.00",
            location="Delhi",
            property_type=Property.PropertyType.SELL,
            amenities="Parking",
            image=SimpleUploadedFile("sale.jpg", b"image", content_type="image/jpeg"),
            seller=self.seller,
        )

        with self.assertRaises(ValidationError) as exc:
            listing.full_clean()

        self.assertEqual(
            exc.exception.message_dict["documents"],
            ["Documents are required for properties for sale."],
        )

    def test_buyer_can_toggle_saved_property(self):
        property_obj = self.make_property(seller=self.seller)
        self.client.force_login(self.buyer)

        response = self.client.post(reverse("core:toggle_saved_property", args=[property_obj.pk]))
        self.assertRedirects(response, reverse("core:property_detail", args=[property_obj.pk]))
        self.assertTrue(
            SavedProperty.objects.filter(buyer=self.buyer, property=property_obj).exists()
        )

        response = self.client.post(reverse("core:toggle_saved_property", args=[property_obj.pk]))
        self.assertRedirects(response, reverse("core:property_detail", args=[property_obj.pk]))
        self.assertFalse(
            SavedProperty.objects.filter(buyer=self.buyer, property=property_obj).exists()
        )

    def test_non_buyer_cannot_have_saved_property_entry(self):
        property_obj = self.make_property(seller=self.seller)
        admin_user = User.objects.create_user(
            username="admin-user",
            password=self.password,
            role=User.Role.ADMIN,
        )

        with self.assertRaises(ValidationError):
            SavedProperty.objects.create(buyer=admin_user, property=property_obj)

    def test_contact_seller_reuses_inquiry_and_marks_unread_messages_read(self):
        property_obj = self.make_property(seller=self.seller, title="Central View")
        self.client.force_login(self.buyer)

        first_response = self.client.post(
            reverse("core:contact_seller", args=[property_obj.pk]),
            {"message": "Is this still available?"},
        )
        inquiry = InquiryChat.objects.get()
        self.assertRedirects(first_response, reverse("core:inquiry_chat_room", args=[inquiry.pk]))
        self.assertEqual(ChatMessage.objects.count(), 1)
        self.assertEqual(inquiry.message, "Is this still available?")

        second_response = self.client.post(
            reverse("core:contact_seller", args=[property_obj.pk]),
            {"message": "Can you share the maintenance details?"},
        )
        inquiry.refresh_from_db()
        self.assertRedirects(second_response, reverse("core:inquiry_chat_room", args=[inquiry.pk]))
        self.assertEqual(InquiryChat.objects.count(), 1)
        self.assertEqual(ChatMessage.objects.count(), 2)
        self.assertEqual(inquiry.message, "Can you share the maintenance details?")

        dashboard_inquiry = get_seller_dashboard_context(self.seller)["inquiries"].get(pk=inquiry.pk)
        self.assertEqual(dashboard_inquiry.unread_count, 2)

        notifications = chat_notifications(self.build_request(self.seller))["chat_notifications"]
        self.assertEqual(notifications["unread_count"], 2)
        self.assertEqual(notifications["items"][0]["preview"], "Can you share the maintenance details?")

        self.client.force_login(self.seller)
        response = self.client.get(reverse("core:inquiry_chat_messages", args=[inquiry.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["messages"]), 2)

        self.assertEqual(
            ChatMessage.objects.filter(inquiry=inquiry, read_at__isnull=False).count(),
            2,
        )
        self.assertEqual(
            chat_notifications(self.build_request(self.seller))["chat_notifications"]["unread_count"],
            0,
        )

    def test_unverified_property_is_hidden_from_other_buyers(self):
        property_obj = self.make_property(
            seller=self.seller,
            title="Private Listing",
            is_verified=False,
        )

        self.client.force_login(self.other_buyer)
        buyer_response = self.client.get(reverse("core:property_detail", args=[property_obj.pk]))
        self.assertEqual(buyer_response.status_code, 404)

        self.client.force_login(self.seller)
        seller_response = self.client.get(reverse("core:property_detail", args=[property_obj.pk]))
        self.assertEqual(seller_response.status_code, 200)
        self.assertContains(seller_response, "Private Listing")

    def test_chat_messages_endpoint_blocks_unrelated_users(self):
        property_obj = self.make_property(seller=self.seller, title="Conversation Test")
        inquiry = InquiryChat.objects.create(
            buyer=self.buyer,
            property=property_obj,
            message="Hello there",
        )

        self.client.force_login(self.other_buyer)
        response = self.client.get(reverse("core:inquiry_chat_messages", args=[inquiry.pk]))

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"], "Forbidden")

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_forgot_password_otp_flow_resets_password(self):
        self.buyer.email = "buyer@example.com"
        self.buyer.save(update_fields=["email"])

        forgot_response = self.client.post(
            reverse("core:forgot_password"),
            {"email": "buyer@example.com"},
        )
        self.assertRedirects(forgot_response, reverse("core:verify_reset_otp"))
        self.assertEqual(len(mail.outbox), 1)

        otp_match = re.search(r"(\d{6})", mail.outbox[0].body)
        self.assertIsNotNone(otp_match)
        otp = otp_match.group(1)

        verify_response = self.client.post(reverse("core:verify_reset_otp"), {"otp": otp})
        self.assertRedirects(verify_response, reverse("core:reset_password"))

        reset_response = self.client.post(
            reverse("core:reset_password"),
            {
                "new_password1": "new-Secure-pass-123",
                "new_password2": "new-Secure-pass-123",
            },
        )
        self.assertRedirects(reset_response, reverse("core:login") + "?reset=1")

        self.buyer.refresh_from_db()
        self.assertTrue(self.buyer.check_password("new-Secure-pass-123"))
        self.assertFalse(PasswordResetOTP.objects.filter(user=self.buyer, is_used=False).exists())

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_forgot_password_rejects_invalid_otp(self):
        self.buyer.email = "buyer@example.com"
        self.buyer.save(update_fields=["email"])

        self.client.post(reverse("core:forgot_password"), {"email": "buyer@example.com"})
        response = self.client.post(reverse("core:verify_reset_otp"), {"otp": "123456"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Invalid OTP. Please try again.")
