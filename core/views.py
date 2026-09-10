import secrets
from collections import Counter
from datetime import datetime
from datetime import timedelta
import re

from django.contrib import messages as django_messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.hashers import check_password, make_password
from django.core.mail import send_mail
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.conf import settings
from django.urls import reverse
from django.utils.timezone import localtime
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from .forms import (
	AdminPanelPropertyForm,
	AdminPanelUserCreateForm,
	AdminPanelUserForm,
	OTPVerificationForm,
	PasswordResetNewPasswordForm,
	PasswordResetRequestForm,
	PlatformSettingsForm,
	PropertyForm,
	RegisterForm,
	UserProfileForm,
)
from .models import ChatMessage, ContactMessage, InquiryChat, PasswordResetOTP, PlatformSettings, PopularCity, Property, SavedProperty, User


PASSWORD_RESET_SESSION_USER = "password_reset_user_id"
PASSWORD_RESET_SESSION_OTP = "password_reset_otp_id"
PASSWORD_RESET_SESSION_VERIFIED = "password_reset_otp_verified"
OTP_VALIDITY_MINUTES = 10


def get_buyer_dashboard_context(user, active_inquiry_id=None):
	inquiries_qs = (
		InquiryChat.objects.filter(buyer=user)
		.select_related("property", "property__seller")
		.prefetch_related("messages", "messages__sender")
		.annotate(
			unread_count=Count(
				"messages",
				filter=Q(messages__read_at__isnull=True) & ~Q(messages__sender_id=user.id),
			)
		)
		.order_by("-created_at")
	)

	inquiries_list = list(inquiries_qs)
	now = timezone.now()
	today = localtime(now).date()

	for inq in inquiries_list:
		all_msgs = list(inq.messages.all())
		if all_msgs:
			last_msg = max(all_msgs, key=lambda m: m.created_at)
			inq.last_activity_time = last_msg.created_at
			inq.last_message_text = last_msg.message
			msg_dt = localtime(last_msg.created_at)
			msg_date = msg_dt.date()
			if msg_date == today:
				inq.last_message_time = msg_dt.strftime("%I:%M %p").lstrip("0")
			elif msg_date == today - timedelta(days=1):
				inq.last_message_time = "Yesterday"
			else:
				inq.last_message_time = msg_dt.strftime("%d %b")
		else:
			inq.last_activity_time = inq.created_at
			inq.last_message_text = inq.message
			msg_dt = localtime(inq.created_at)
			inq.last_message_time = msg_dt.strftime("%d %b")

	inquiries_list.sort(key=lambda x: x.last_activity_time, reverse=True)

	active_inquiry = None
	if active_inquiry_id and str(active_inquiry_id) != '0':
		try:
			active_inquiry = next((i for i in inquiries_list if i.pk == int(active_inquiry_id)), None)
		except (ValueError, TypeError):
			pass

	active_messages = []
	latest_message_id = 0
	if active_inquiry:
		active_messages = add_chat_date_metadata(list(active_inquiry.messages.select_related("sender")))
		if active_messages:
			latest_message_id = active_messages[-1].id
		mark_incoming_messages_read(active_inquiry, user)

	return {
		"inquiries": inquiries_list,
		"active_inquiry": active_inquiry,
		"active_messages": active_messages,
		"latest_message_id": latest_message_id,
		"saved_count": SavedProperty.objects.filter(buyer=user).count(),
	}


def get_seller_chat_context(user, active_inquiry_id=None):
	inquiries_qs = (
		InquiryChat.objects.filter(property__seller=user)
		.select_related("buyer", "property", "property__seller")
		.prefetch_related("messages", "messages__sender")
		.annotate(
			unread_count=Count(
				"messages",
				filter=Q(messages__read_at__isnull=True) & ~Q(messages__sender_id=user.id),
			)
		)
		.order_by("-created_at")
	)

	inquiries_list = list(inquiries_qs)
	now = timezone.now()
	today = localtime(now).date()

	for inq in inquiries_list:
		all_msgs = list(inq.messages.all())
		if all_msgs:
			last_msg = max(all_msgs, key=lambda m: m.created_at)
			inq.last_activity_time = last_msg.created_at
			inq.last_message_text = last_msg.message
			msg_dt = localtime(last_msg.created_at)
			msg_date = msg_dt.date()
			if msg_date == today:
				inq.last_message_time = msg_dt.strftime("%I:%M %p").lstrip("0")
			elif msg_date == today - timedelta(days=1):
				inq.last_message_time = "Yesterday"
			else:
				inq.last_message_time = msg_dt.strftime("%d %b")
		else:
			inq.last_activity_time = inq.created_at
			inq.last_message_text = inq.message
			msg_dt = localtime(inq.created_at)
			inq.last_message_time = msg_dt.strftime("%d %b")

	inquiries_list.sort(key=lambda x: x.last_activity_time, reverse=True)

	active_inquiry = None
	if active_inquiry_id and str(active_inquiry_id) != '0':
		try:
			active_inquiry = next((i for i in inquiries_list if i.pk == int(active_inquiry_id)), None)
		except (ValueError, TypeError):
			pass

	active_messages = []
	latest_message_id = 0
	if active_inquiry:
		active_messages = add_chat_date_metadata(list(active_inquiry.messages.select_related("sender")))
		if active_messages:
			latest_message_id = active_messages[-1].id
		mark_incoming_messages_read(active_inquiry, user)

	return {
		"inquiries": inquiries_list,
		"active_inquiry": active_inquiry,
		"active_messages": active_messages,
		"latest_message_id": latest_message_id,
	}


def get_seller_dashboard_context(user, form=None):
	if form is None:
		form = PropertyForm()

	properties = Property.objects.filter(seller=user).order_by("-created_at")
	inquiries = (
		InquiryChat.objects.filter(property__seller=user)
		.select_related("buyer", "property")
		.annotate(
			unread_count=Count(
				"messages",
				filter=Q(messages__read_at__isnull=True) & ~Q(messages__sender_id=user.id),
			)
		)
	)
	return {
		"form": form,
		"properties": properties,
		"inquiries": inquiries,
	}


def redirect_by_role(user):
	if user.is_superuser or user.role == User.Role.ADMIN:
		return redirect("core:admin_dashboard")
	return redirect("core:home")


def get_next_url(request):
	next_url = request.POST.get("next") or request.GET.get("next")
	if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
		return next_url
	return ""


def get_inquiry_for_user(request_user, pk):
	inquiry = get_object_or_404(
		InquiryChat.objects.select_related("buyer", "property", "property__seller"), pk=pk
	)
	is_buyer_owner = inquiry.buyer_id == request_user.id
	is_seller_owner = inquiry.property.seller_id == request_user.id
	return inquiry, is_buyer_owner, is_seller_owner


def serialize_chat_message(message, request_user):
	sender_role = "buyer" if message.sender_id == message.inquiry.buyer_id else "seller"
	message_date = localtime(message.created_at).date()
	today = timezone.localdate()
	if message_date == today:
		date_label = "Today"
	elif message_date == today - timedelta(days=1):
		date_label = "Yesterday"
	else:
		date_label = message_date.strftime("%d/%m/%Y")
	return {
		"id": message.id,
		"message": message.message,
		"sender_id": message.sender_id,
		"sender_name": message.sender.username,
		"sender_role": sender_role,
		"is_self": message.sender_id == request_user.id,
		"created_at": localtime(message.created_at).isoformat(),
		"created_at_label": localtime(message.created_at).strftime("%b %d, %Y, %I:%M %p"),
		"created_at_time_label": localtime(message.created_at).strftime("%I:%M %p").lstrip("0"),
		"date_key": message_date.isoformat(),
		"date_label": date_label,
		"is_seen": message.read_at is not None,
	}


def add_chat_date_metadata(messages):
	last_date_key = None
	today = timezone.localdate()
	for message in messages:
		message_date = localtime(message.created_at).date()
		date_key = message_date.isoformat()
		if message_date == today:
			date_label = "Today"
		elif message_date == today - timedelta(days=1):
			date_label = "Yesterday"
		else:
			date_label = message_date.strftime("%d/%m/%Y")
		message.date_key = date_key
		message.date_label = date_label
		message.show_date_separator = date_key != last_date_key
		last_date_key = date_key
	return messages


def mark_incoming_messages_read(inquiry, user):
	inquiry.messages.exclude(sender=user).filter(read_at__isnull=True).update(
		read_at=timezone.now()
	)


def home(request):
	verified_properties = (
		Property.objects.filter(is_verified=True)
		.select_related("seller")
		.order_by("-created_at")
	)
	featured_properties = []
	featured_seller_ids = set()
	for property_obj in verified_properties:
		if property_obj.seller_id in featured_seller_ids:
			continue
		featured_properties.append(property_obj)
		featured_seller_ids.add(property_obj.seller_id)
		if len(featured_properties) == 6:
			break

	if len(featured_properties) < 6:
		featured_property_ids = {property_obj.id for property_obj in featured_properties}
		for property_obj in verified_properties:
			if property_obj.id not in featured_property_ids:
				featured_properties.append(property_obj)
				featured_property_ids.add(property_obj.id)
			if len(featured_properties) == 6:
				break

	popular_cities = PopularCity.objects.all()[:6]
	trusted_sellers = (
		User.objects.filter(role=User.Role.SELLER)
		.annotate(prop_count=Count("properties", filter=Q(properties__is_verified=True)))
		.order_by("-prop_count")[:4]
	)
	saved_property_ids = set()
	if request.user.is_authenticated and request.user.role == User.Role.BUYER:
		saved_property_ids = set(
			SavedProperty.objects.filter(buyer=request.user).values_list("property_id", flat=True)
		)

	# Check if we need to open a modal
	modal_to_open = request.GET.get("modal")

	return render(
		request,
		"core/home.html",
		{
			"featured_properties": featured_properties,
			"popular_cities": popular_cities,
			"trusted_sellers": trusted_sellers,
			"property_types": Property.PropertyType.choices,
			"saved_property_ids": saved_property_ids,
			"modal_to_open": modal_to_open,
		},
	)



def login_view(request):
	if request.user.is_authenticated:
		return redirect_by_role(request.user)

	if request.method == "POST":
		# Handle modal form submission
		email = request.POST.get("email", "").strip()
		password = request.POST.get("password", "").strip()
		role = request.POST.get("role", "").strip()
		
		user_obj = User.objects.filter(
			Q(email__iexact=email) | Q(username__iexact=email)
		).first()
		auth_username = user_obj.username if user_obj else email
		
		user = authenticate(request, username=auth_username, password=password)
		if user is not None:
			# Verify the user has the selected role
			user_role = "buyer" if user.role == User.Role.BUYER else "seller"
			if user_role != role:
				is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
				if is_ajax:
					return JsonResponse({'success': False, 'message': 'Invalid account role for this email.'}, status=400)
				django_messages.error(request, "Invalid account role for this email.")
				return redirect(f"{reverse('core:home')}?modal=login")
			
			login(request, user)
			next_url = get_next_url(request)
			if next_url:
				return redirect(next_url)
			return redirect_by_role(user)
		
		is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
		if is_ajax:
			return JsonResponse({'success': False, 'message': 'Invalid email or password.'}, status=400)
		django_messages.error(request, "Invalid email or password.")
		return redirect(f"{reverse('core:home')}?modal=login")

	# GET request - redirect to home with login modal trigger
	return redirect(f"{reverse('core:home')}?modal=login")


def admin_login_view(request):
	if request.user.is_authenticated:
		if request.user.is_superuser or request.user.role == User.Role.ADMIN:
			return redirect("core:admin_dashboard")
		return redirect("core:dashboard")

	next_url = get_next_url(request)
	context = {"next_url": next_url}

	if request.method == "POST":
		login_input = (
			request.POST.get("login_id", "").strip()
			or request.POST.get("email", "").strip()
			or request.POST.get("username", "").strip()
		)
		password = request.POST.get("password", "")
		context["entered_login_id"] = login_input

		user_obj = User.objects.filter(
			Q(email__iexact=login_input) | Q(username__iexact=login_input)
		).first()
		auth_username = user_obj.username if user_obj else login_input

		user = authenticate(request, username=auth_username, password=password)
		if user is not None:
			if not (user.is_superuser or user.role == User.Role.ADMIN):
				context["error"] = "Access denied. Only Administrator accounts can log in here."
				return render(request, "core/admin_login.html", context)

			login(request, user)
			if next_url:
				return redirect(next_url)
			return redirect("core:admin_dashboard")
		context["error"] = "Invalid admin username/email or password."

	return render(request, "core/admin_login.html", context)


def forgot_password_view(request):
	if request.user.is_authenticated:
		return redirect_by_role(request.user)

	if request.method == "POST":
		form = PasswordResetRequestForm(request.POST)
		if form.is_valid():
			email = form.cleaned_data["email"].strip()
			user = User.objects.filter(email__iexact=email).first()
			
			is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
			
			if user is None:
				if is_ajax:
					return JsonResponse({'success': False, 'message': 'Email is not registered.'}, status=400)
				django_messages.error(request, "Email is not registered.")
				return redirect(f"{reverse('core:home')}?modal=forgot-password")
			
			# Clear previous unused OTPs
			PasswordResetOTP.objects.filter(user=user, is_used=False).update(is_used=True)
			otp = f"{secrets.randbelow(900000) + 100000}"
			expires_at = timezone.now() + timedelta(minutes=OTP_VALIDITY_MINUTES)
			otp_obj = PasswordResetOTP.objects.create(
				user=user,
				otp_hash=make_password(otp),
				expires_at=expires_at,
			)

			from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@urbanhunt.local")
			try:
				send_mail(
					subject="Urban Hunt Password Reset OTP",
					message=(
						f"Hello {user.username},\n\n"
						f"Use this OTP to reset your Urban Hunt password: {otp}\n"
						f"This OTP will expire in {OTP_VALIDITY_MINUTES} minutes.\n\n"
						"If you did not request this, please ignore this email."
					),
					from_email=from_email,
					recipient_list=[user.email],
					fail_silently=False,
				)
			except Exception as e:
				otp_obj.delete()
				if is_ajax:
					return JsonResponse({'success': False, 'message': f'Failed to send OTP email: {str(e)}'}, status=400)
				django_messages.error(request, f"Failed to send OTP email. Error: {str(e)}")
				return redirect(f"{reverse('core:home')}?modal=forgot-password")
			
			# Email sent successfully
			request.session[PASSWORD_RESET_SESSION_USER] = user.id
			request.session[PASSWORD_RESET_SESSION_OTP] = otp_obj.id
			request.session[PASSWORD_RESET_SESSION_VERIFIED] = False
			
			if is_ajax:
				return JsonResponse({'success': True, 'message': 'OTP sent successfully. Check your email.'})
			
			return redirect(f"{reverse('core:home')}?modal=verify-otp")
		else:
			if is_ajax:
				errors_list = []
				for field, errors in form.errors.items():
					for error in errors:
						errors_list.append(f"{field}: {error}")
				return JsonResponse({'success': False, 'message': errors_list[0] if errors_list else 'Invalid form'}, status=400)
			
			for field, errors in form.errors.items():
				for error in errors:
					django_messages.error(request, f"{field}: {error}")
			return redirect(f"{reverse('core:home')}?modal=forgot-password")
	
	# GET request - redirect to home with forgot password modal
	return redirect(f"{reverse('core:home')}?modal=forgot-password")


def verify_reset_otp_view(request):
	if request.user.is_authenticated:
		return redirect_by_role(request.user)

	user_id = request.session.get(PASSWORD_RESET_SESSION_USER)
	otp_id = request.session.get(PASSWORD_RESET_SESSION_OTP)
	
	is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
	
	if not user_id or not otp_id:
		if is_ajax:
			return JsonResponse({'success': False, 'message': 'Session expired. Please start over.'}, status=400)
		return redirect(f"{reverse('core:home')}?modal=forgot-password")

	user = get_object_or_404(User, pk=user_id)
	otp_obj = PasswordResetOTP.objects.filter(pk=otp_id, user=user).first()
	
	if otp_obj is None or otp_obj.is_used or otp_obj.is_expired():
		request.session.pop(PASSWORD_RESET_SESSION_OTP, None)
		request.session[PASSWORD_RESET_SESSION_VERIFIED] = False
		if is_ajax:
			return JsonResponse({'success': False, 'message': 'OTP expired or invalid. Please request a new OTP.'}, status=400)
		return redirect(f"{reverse('core:home')}?modal=forgot-password")

	if request.method == "POST":
		form = OTPVerificationForm(request.POST)
		if form.is_valid():
			otp = form.cleaned_data["otp"]
			if check_password(otp, otp_obj.otp_hash):
				otp_obj.is_used = True
				otp_obj.save(update_fields=["is_used"])
				request.session[PASSWORD_RESET_SESSION_VERIFIED] = True
				
				if is_ajax:
					return JsonResponse({'success': True, 'message': 'OTP verified successfully'})
				
				return redirect(f"{reverse('core:home')}?modal=reset-password")
			else:
				if is_ajax:
					return JsonResponse({'success': False, 'message': 'Invalid OTP. Please try again.'}, status=400)
				django_messages.error(request, "Invalid OTP. Please try again.")
				return redirect(f"{reverse('core:home')}?modal=verify-otp")
		else:
			errors_list = []
			for field, errors in form.errors.items():
				for error in errors:
					errors_list.append(f"{field}: {error}")
			
			if is_ajax:
				return JsonResponse({'success': False, 'message': errors_list[0] if errors_list else 'Invalid form'}, status=400)
			
			for error_msg in errors_list:
				django_messages.error(request, error_msg)
			return redirect(f"{reverse('core:home')}?modal=verify-otp")

	# GET request - redirect to home with verify-otp modal
	return redirect(f"{reverse('core:home')}?modal=verify-otp")


def reset_password_view(request):
	if request.user.is_authenticated:
		return redirect_by_role(request.user)

	user_id = request.session.get(PASSWORD_RESET_SESSION_USER)
	is_verified = request.session.get(PASSWORD_RESET_SESSION_VERIFIED, False)
	
	is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
	
	if not user_id or not is_verified:
		if is_ajax:
			return JsonResponse({'success': False, 'message': 'Session expired. Please start the reset process again.'}, status=400)
		return redirect(f"{reverse('core:home')}?modal=forgot-password")

	user = get_object_or_404(User, pk=user_id)
	
	if request.method == "POST":
		form = PasswordResetNewPasswordForm(user, request.POST)
		if form.is_valid():
			form.save()
			request.session.pop(PASSWORD_RESET_SESSION_USER, None)
			request.session.pop(PASSWORD_RESET_SESSION_OTP, None)
			request.session.pop(PASSWORD_RESET_SESSION_VERIFIED, None)
			
			if is_ajax:
				return JsonResponse({'success': True, 'message': 'Password updated successfully'})
			
			return redirect(f"{reverse('core:home')}?modal=login&reset=1")
		else:
			errors_list = []
			for field, errors in form.errors.items():
				for error in errors:
					errors_list.append(f"{field}: {error}")
			
			if is_ajax:
				return JsonResponse({'success': False, 'message': errors_list[0] if errors_list else 'Failed to update password'}, status=400)
			
			for error_msg in errors_list:
				django_messages.error(request, error_msg)
			return redirect(f"{reverse('core:home')}?modal=reset-password")
	else:
		form = PasswordResetNewPasswordForm(user)

	# GET request - redirect to home with reset-password modal
	return redirect(f"{reverse('core:home')}?modal=reset-password")


def _mask_email(email):
	if "@" not in email:
		return email
	local, domain = email.split("@", 1)
	if len(local) <= 2:
		masked_local = local[0] + "*" * (len(local) - 1)
	else:
		masked_local = local[:2] + "*" * (len(local) - 2)
	return f"{masked_local}@{domain}"


def register_view(request):
	if request.user.is_authenticated:
		return redirect_by_role(request.user)
	platform_settings = PlatformSettings.objects.first()
	if platform_settings and not platform_settings.allow_user_registration:
		django_messages.error(request, "New user registration is currently disabled.")
		return redirect("core:home")

	if request.method == "POST":
		# Handle modal form submission
		form = RegisterForm(request.POST)
		if form.is_valid():
			user = form.save()
			# Log the user in after registration
			login(request, user)
			return redirect_by_role(user)
		else:
			is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
			if is_ajax:
				return JsonResponse({'success': False, 'errors': form.errors}, status=400)
			# For non-AJAX, add errors to messages and redirect back to register modal
			for field, errors in form.errors.items():
				for error in errors:
					django_messages.error(request, f"{field}: {error}")
			return redirect(f"{reverse('core:home')}?modal=register")
	
	# GET request - redirect to home with register modal trigger
	return redirect(f"{reverse('core:home')}?modal=register")


@login_required
def logout_view(request):
	if request.method == "POST":
		logout(request)
		return redirect("core:home")
	return redirect_by_role(request.user)


@login_required
def dashboard(request):
	if request.user.is_superuser or request.user.role == User.Role.ADMIN:
		return redirect_by_role(request.user)

	context = {}
	if request.user.role == User.Role.BUYER:
		active_inquiry_id = request.GET.get("inquiry")
		context.update(get_buyer_dashboard_context(request.user, active_inquiry_id=active_inquiry_id))
	elif request.user.role == User.Role.SELLER:
		tab = request.GET.get("tab")
		active_inquiry_id = request.GET.get("inquiry")

		if tab == "chat" or active_inquiry_id is not None:
			context["active_tab"] = "chat"
			context.update(get_seller_chat_context(request.user, active_inquiry_id=active_inquiry_id))
		else:
			context["active_tab"] = "listings"
			if request.method == "POST":
				form = PropertyForm(request.POST, request.FILES)
				if form.is_valid():
					property_obj = form.save(commit=False)
					property_obj.seller = request.user
					property_obj.save()
					
					# Check if it's an AJAX request
					is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
					if is_ajax:
						return JsonResponse({
							'success': True,
							'message': 'Property created successfully!',
							'property': {
								'id': property_obj.id,
								'title': property_obj.title,
								'price': str(property_obj.price),
								'property_type': property_obj.get_property_type_display(),
								'status': property_obj.status,
								'status_display': property_obj.get_status_display(),
								'is_verified': property_obj.is_verified,
								'image_url': property_obj.image.url if property_obj.image else None,
							}
						})
					return redirect("core:dashboard")
				else:
					# For AJAX requests, return form errors
					is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
					if is_ajax:
						return JsonResponse({
							'success': False,
							'errors': form.errors
						}, status=400)
			else:
				form = PropertyForm()
			context.update(get_seller_dashboard_context(request.user, form=form))
	return render(request, "core/dashboard.html", context)


@login_required
def buyer_dashboard(request):
	if request.user.is_superuser or request.user.role != User.Role.BUYER:
		return redirect_by_role(request.user)
	return redirect("core:dashboard")


@login_required
def saved_properties(request):
	is_buyer = not request.user.is_superuser and request.user.role == User.Role.BUYER
	saved_items = SavedProperty.objects.none()

	if is_buyer:
		saved_items = SavedProperty.objects.filter(buyer=request.user).select_related(
			"property", "property__seller"
		)

	return render(request, "core/saved_properties.html", {
		"saved_items": saved_items,
		"can_save_properties": is_buyer,
	})


@login_required
def seller_dashboard(request):
	if request.user.is_superuser or request.user.role != User.Role.SELLER:
		return redirect_by_role(request.user)
	return redirect("core:dashboard")


@login_required
def edit_property(request, pk):
	if request.user.role != User.Role.SELLER:
		return redirect_by_role(request.user)

	property_obj = get_object_or_404(Property, pk=pk, seller=request.user)
	if request.method == "POST":
		form = PropertyForm(request.POST, request.FILES, instance=property_obj)
		if form.is_valid():
			updated = form.save(commit=False)
			updated.seller = request.user
			updated.save()
			
			is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
			if is_ajax:
				return JsonResponse({
					'success': True,
					'message': 'Property updated successfully.',
					'property_id': pk
				})
			
			return redirect("core:dashboard")
		else:
			is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
			if is_ajax:
				return JsonResponse({
					'success': False,
					'errors': form.errors
				}, status=400)
	else:
		form = PropertyForm(instance=property_obj)

	return render(
		request,
		"core/property_form.html",
		{"form": form, "property": property_obj, "is_edit": True},
	)


@login_required
def delete_property(request, pk):
	if request.user.role != User.Role.SELLER:
		return redirect_by_role(request.user)

	property_obj = get_object_or_404(Property, pk=pk, seller=request.user)
	if request.method == "POST":
		property_obj.delete()
		is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
		if is_ajax:
			return JsonResponse({'success': True, 'message': 'Property deleted successfully.'})
		return redirect("core:dashboard")

	return render(request, "core/property_form.html", {"property": property_obj, "is_delete": True})


@login_required
def update_property_status(request, pk):
	"""Allow sellers to update property status"""
	if request.method != "POST":
		return redirect("core:property_detail", pk=pk)
	if request.user.role != User.Role.SELLER:
		return redirect_by_role(request.user)

	property_obj = get_object_or_404(Property, pk=pk, seller=request.user)
	new_status = request.POST.get("status", "").strip()

	if new_status not in [choice[0] for choice in Property.PropertyStatus.choices]:
		new_data = {'success': False, 'message': 'Invalid status.'}
		if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
			return JsonResponse(new_data, status=400)
		django_messages.error(request, "Invalid status.")
		return redirect("core:dashboard")

	property_obj.status = new_status
	property_obj.save(update_fields=["status"])
	
	is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
	if is_ajax:
		return JsonResponse({
			'success': True,
			'message': f"Property status updated to '{property_obj.get_status_display()}'.",
			'status': new_status,
			'status_display': property_obj.get_status_display(),
			'property_id': pk
		})
	
	django_messages.success(request, f"Property status updated to '{property_obj.get_status_display()}'.")
	next_url = request.POST.get("next")
	if next_url:
		return redirect(next_url)
	return redirect("core:dashboard")


@login_required
def profile_view(request):
	user = request.user
	if request.method == "POST":
		if "remove_avatar" in request.POST:
			if user.avatar:
				user.avatar.delete(save=False)
				user.avatar = None
				user.save(update_fields=["avatar"])
				django_messages.success(request, "Profile photo removed successfully.")
				return redirect("core:profile")

		form = UserProfileForm(request.POST, request.FILES, instance=user)
		if form.is_valid():
			form.save()
			django_messages.success(request, "Your profile information has been updated successfully!")
			return redirect("core:profile")
		else:
			django_messages.error(request, "Please correct the errors in the form below.")
	else:
		form = UserProfileForm(instance=user)

	saved_count = 0
	listed_count = 0
	inquiry_count = 0

	if user.role == User.Role.BUYER:
		saved_count = SavedProperty.objects.filter(buyer=user).count()
		inquiry_count = InquiryChat.objects.filter(buyer=user).count()
	elif user.role == User.Role.SELLER:
		listed_count = Property.objects.filter(seller=user).count()
		inquiry_count = InquiryChat.objects.filter(property__seller=user).count()

	context = {
		"form": form,
		"saved_count": saved_count,
		"listed_count": listed_count,
		"inquiry_count": inquiry_count,
	}
	return render(request, "core/profile.html", context)


@login_required
def change_password_view(request):
	if request.method == "POST":
		is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
		
		current_password = request.POST.get("current_password", "").strip()
		new_password = request.POST.get("new_password", "").strip()
		confirm_password = request.POST.get("confirm_password", "").strip()
		
		# Validate current password
		if not check_password(current_password, request.user.password):
			error_msg = "Current password is incorrect."
			if is_ajax:
				return JsonResponse({'success': False, 'message': error_msg}, status=400)
			django_messages.error(request, error_msg)
			return render(request, "core/change_password.html")
		
		# Validate new password
		if len(new_password) < 8:
			error_msg = "New password must be at least 8 characters long."
			if is_ajax:
				return JsonResponse({'success': False, 'message': error_msg}, status=400)
			django_messages.error(request, error_msg)
			return render(request, "core/change_password.html")
		
		# Check if passwords match
		if new_password != confirm_password:
			error_msg = "New passwords do not match."
			if is_ajax:
				return JsonResponse({'success': False, 'message': error_msg}, status=400)
			django_messages.error(request, error_msg)
			return render(request, "core/change_password.html")
		
		# Check if new password is same as current
		if current_password == new_password:
			error_msg = "New password must be different from current password."
			if is_ajax:
				return JsonResponse({'success': False, 'message': error_msg}, status=400)
			django_messages.error(request, error_msg)
			return render(request, "core/change_password.html")
		
		# Update password
		request.user.set_password(new_password)
		request.user.save()
		success_msg = "Your password has been changed successfully!"
		
		if is_ajax:
			return JsonResponse({'success': True, 'message': success_msg})
		
		django_messages.success(request, success_msg)
		return redirect("core:profile")
	
	return render(request, "core/change_password.html")


def property_list(request):
	properties = Property.objects.select_related("seller")
	show_unverified = False
	if request.user.is_authenticated:
		if request.user.is_superuser or request.user.role == User.Role.ADMIN:
			show_unverified = True
		elif request.user.role == User.Role.SELLER:
			show_unverified = True
			properties = properties.filter(Q(is_verified=True) | Q(seller=request.user))
		else:
			properties = properties.filter(is_verified=True)
	else:
		properties = properties.filter(is_verified=True)

	q = request.GET.get("q", "").strip()
	location = request.GET.get("location", "").strip()
	min_price = request.GET.get("min_price", "").strip()
	max_price = request.GET.get("max_price", "").strip()
	property_type = request.GET.get("property_type", "").strip()
	amenities = request.GET.get("amenities", "").strip()

	if q:
		properties = properties.filter(
			Q(title__icontains=q)
			| Q(location__icontains=q)
			| Q(description__icontains=q)
			| Q(seller__username__icontains=q)
			| Q(seller__first_name__icontains=q)
			| Q(seller__last_name__icontains=q)
		)

	if location:
		properties = properties.filter(location__icontains=location)
	if min_price:
		properties = properties.filter(price__gte=min_price)
	if max_price:
		properties = properties.filter(price__lte=max_price)
	if property_type:
		properties = properties.filter(property_type=property_type)
	if amenities:
		for amenity in [item.strip() for item in amenities.split(",") if item.strip()]:
			properties = properties.filter(amenities__icontains=amenity)

	saved_property_ids = set()
	if request.user.is_authenticated and request.user.role == User.Role.BUYER:
		saved_property_ids = set(
			SavedProperty.objects.filter(buyer=request.user).values_list("property_id", flat=True)
		)

	ordered_properties = properties.order_by("-created_at")
	paginator = Paginator(ordered_properties, 6)
	page_number = request.GET.get("page", 1)
	page_obj = paginator.get_page(page_number)

	context = {
		"properties": page_obj,
		"page_obj": page_obj,
		"total_count": ordered_properties.count(),
		"filters": {
			"q": q,
			"location": location,
			"min_price": min_price,
			"max_price": max_price,
			"property_type": property_type,
			"amenities": amenities,
		},
		"property_type_choices": Property.PropertyType.choices,
		"show_unverified": show_unverified,
		"saved_property_ids": saved_property_ids,
	}
	return render(request, "core/property_list.html", context)


def property_detail(request, pk):
	properties = Property.objects.select_related("seller")
	if request.user.is_authenticated:
		can_view_unverified = request.user.is_superuser or request.user.role == User.Role.ADMIN
		if not can_view_unverified:
			can_view_unverified = (
				request.user.role == User.Role.SELLER
				and properties.filter(pk=pk, seller=request.user).exists()
			)
		if can_view_unverified:
			property_obj = get_object_or_404(properties, pk=pk)
		else:
			property_obj = get_object_or_404(properties, pk=pk, is_verified=True)
	else:
		property_obj = get_object_or_404(properties, pk=pk, is_verified=True)

	is_saved = False
	if request.user.is_authenticated and request.user.role == User.Role.BUYER:
		is_saved = SavedProperty.objects.filter(buyer=request.user, property=property_obj).exists()

	return render(
		request,
		"core/property_detail.html",
		{"property": property_obj, "is_saved": is_saved},
	)


@login_required
def toggle_saved_property(request, pk):
	if request.method != "POST":
		return redirect("core:property_detail", pk=pk)
	if request.user.is_superuser or request.user.role != User.Role.BUYER:
		return redirect_by_role(request.user)

	property_obj = get_object_or_404(Property, pk=pk, is_verified=True)
	saved = SavedProperty.objects.filter(buyer=request.user, property=property_obj)
	is_saved = False
	
	if saved.exists():
		saved.delete()
		message = "Property removed from saved."
		button_text = "Save Property"
	else:
		SavedProperty.objects.create(buyer=request.user, property=property_obj)
		is_saved = True
		message = "Property saved successfully."
		button_text = "Remove from Saved"

	is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
	if is_ajax:
		return JsonResponse({
			'success': True,
			'message': message,
			'is_saved': is_saved,
			'action': 'saved' if is_saved else 'removed',
			'button_text': button_text
		})

	next_url = get_next_url(request)
	if next_url:
		return redirect(next_url)
	return redirect("core:property_detail", pk=pk)


@login_required
def contact_seller(request, pk):
	if request.method != "POST":
		return redirect("core:property_detail", pk=pk)
	if request.user.is_superuser or request.user.role != User.Role.BUYER:
		return redirect_by_role(request.user)

	property_obj = get_object_or_404(Property, pk=pk, is_verified=True)
	is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
	
	# Check if property is available
	if property_obj.status != Property.PropertyStatus.AVAILABLE:
		status_msg = property_obj.get_status_display().lower()
		if property_obj.status == Property.PropertyStatus.SOLD:
			error_msg = f"This property has been sold and is no longer available for inquiry. Please explore other available properties."
		elif property_obj.status == Property.PropertyStatus.RENTED:
			error_msg = f"This property is currently on rent and not available for new inquiries."
		else:
			error_msg = f"This property is currently {status_msg}. You cannot contact the seller."
		
		if is_ajax:
			return JsonResponse({'success': False, 'message': error_msg}, status=400)
		
		django_messages.error(request, error_msg)
		return redirect("core:property_detail", pk=pk)
	
	message = request.POST.get("message", "").strip()
	if not message:
		message = "Hi, I am interested in this property. Please share more details."

	inquiry = InquiryChat.objects.filter(buyer=request.user, property=property_obj).first()
	if inquiry is None:
		inquiry = InquiryChat.objects.create(
			buyer=request.user,
			property=property_obj,
			message=message,
		)
	elif inquiry.message != message:
		inquiry.message = message
		inquiry.save(update_fields=["message"])

	ChatMessage.objects.create(inquiry=inquiry, sender=request.user, message=message)
	
	if is_ajax:
		return JsonResponse({
			'success': True,
			'message': 'Message sent to seller successfully.',
			'inquiry_id': inquiry.pk,
			'chat_url': reverse('core:inquiry_chat_room', kwargs={'pk': inquiry.pk})
		})
	
	return redirect("core:inquiry_chat_room", pk=inquiry.pk)


@login_required
def inquiry_chat_room(request, pk):
	inquiry, is_buyer_owner, is_seller_owner = get_inquiry_for_user(request.user, pk)
	if not (is_buyer_owner or is_seller_owner):
		return redirect_by_role(request.user)

	if is_buyer_owner:
		return redirect(f"{reverse('core:dashboard')}?inquiry={pk}")

	if request.method == "POST":
		# Prevent buyers from messaging on unavailable properties
		if is_buyer_owner and inquiry.property.status != Property.PropertyStatus.AVAILABLE:
			django_messages.error(request, f"This property is currently {inquiry.property.get_status_display().lower()} and is no longer available for messaging.")
			return redirect("core:inquiry_chat_room", pk=inquiry.pk)
		
		message = request.POST.get("message", "").strip()
		if message:
			ChatMessage.objects.create(inquiry=inquiry, sender=request.user, message=message)
		return redirect("core:inquiry_chat_room", pk=inquiry.pk)

	messages = inquiry.messages.select_related("sender")
	mark_incoming_messages_read(inquiry, request.user)
	return render(
		request,
		"core/inquiry_chat_room.html",
		{
			"inquiry": inquiry,
			"messages": messages,
			"is_buyer_owner": is_buyer_owner,
			"is_seller_owner": is_seller_owner,
			"latest_message_id": messages.last().id if messages.exists() else 0,
		},
	)


@login_required
def inquiry_chat_messages(request, pk):
	inquiry, is_buyer_owner, is_seller_owner = get_inquiry_for_user(request.user, pk)
	if not (is_buyer_owner or is_seller_owner):
		return JsonResponse({"error": "Forbidden"}, status=403)

	if request.method == "POST":
		message_text = request.POST.get("message", "").strip()
		if not message_text:
			return JsonResponse({"error": "Message is required."}, status=400)

		# Check if property is still available for buyer messages
		if is_buyer_owner and inquiry.property.status != Property.PropertyStatus.AVAILABLE:
			return JsonResponse({
				"error": f"This property is {inquiry.property.get_status_display().lower()}. Cannot send messages."
			}, status=400)

		message = ChatMessage.objects.create(
			inquiry=inquiry,
			sender=request.user,
			message=message_text,
		)
		message = (
			ChatMessage.objects.select_related("sender", "inquiry", "inquiry__buyer")
			.get(pk=message.pk)
		)
		return JsonResponse({"message": serialize_chat_message(message, request.user)}, status=201)

	since_id = request.GET.get("since_id", "").strip()
	message_qs = inquiry.messages.select_related("sender", "inquiry", "inquiry__buyer")
	if since_id.isdigit():
		message_qs = message_qs.filter(pk__gt=int(since_id))

	message_list = list(message_qs)
	unread_ids = [
		item.id
		for item in message_list
		if item.sender_id != request.user.id and item.read_at is None
	]
	if unread_ids:
		ChatMessage.objects.filter(pk__in=unread_ids).update(read_at=timezone.now())

	messages = [serialize_chat_message(item, request.user) for item in message_list]
	seen_message_ids = list(
		inquiry.messages.filter(sender=request.user, read_at__isnull=False).values_list("id", flat=True)
	)
	return JsonResponse({"messages": messages, "seen_message_ids": seen_message_ids})


# ──────────────────────────────────────────────────────────────
# Admin Panel helpers
# ──────────────────────────────────────────────────────────────

def _require_admin(request):
	"""Return None if the user is an admin, otherwise return a redirect."""
	if not request.user.is_authenticated:
		return redirect(f"/admin-login/?next={request.path}")
	if not (request.user.is_superuser or request.user.role == User.Role.ADMIN):
		return redirect("core:home")
	return None


def _paginate(qs, request, per_page=25):
	paginator = Paginator(qs, per_page)
	page_number = request.GET.get("page", 1)
	return paginator.get_page(page_number)


def _admin_stats():
	return {
		"total_users": User.objects.count(),
		"buyers": User.objects.filter(role=User.Role.BUYER).count(),
		"sellers": User.objects.filter(role=User.Role.SELLER).count(),
		"verified_properties": Property.objects.filter(is_verified=True).count(),
		"pending_properties": Property.objects.filter(is_verified=False).count(),
		"total_inquiries": InquiryChat.objects.count(),
		"total_messages": ChatMessage.objects.count(),
		"saved_properties": SavedProperty.objects.filter(buyer__role=User.Role.BUYER).count(),
	}


@login_required
def api_mark_notifications_read(request):
	if request.method == "POST":
		user = request.user
		if user.role == User.Role.BUYER:
			inquiries = InquiryChat.objects.filter(buyer=user)
		elif user.role == User.Role.SELLER:
			inquiries = InquiryChat.objects.filter(property__seller=user)
		else:
			return JsonResponse({"status": "ignored"})
		
		ChatMessage.objects.filter(
			inquiry__in=inquiries, read_at__isnull=True
		).exclude(sender=user).update(read_at=timezone.now())
		
		return JsonResponse({"status": "success"})
	return JsonResponse({"error": "Method not allowed"}, status=405)


@login_required
def api_mark_inquiry_read(request, pk):
	"""Mark messages in a specific inquiry as read and return updated notification count"""
	if request.method != "POST":
		return JsonResponse({"error": "Method not allowed"}, status=405)
	
	inquiry, is_buyer_owner, is_seller_owner = get_inquiry_for_user(request.user, pk)
	if not (is_buyer_owner or is_seller_owner):
		return JsonResponse({"error": "Unauthorized"}, status=403)
	
	# Mark messages as read
	mark_incoming_messages_read(inquiry, request.user)
	
	# Calculate updated notification count
	user = request.user
	if user.role == User.Role.BUYER:
		inquiries = InquiryChat.objects.filter(buyer=user)
	elif user.role == User.Role.SELLER:
		inquiries = InquiryChat.objects.filter(property__seller=user)
	else:
		inquiries = InquiryChat.objects.none()
	
	unread_count = ChatMessage.objects.filter(
		inquiry__in=inquiries, read_at__isnull=True
	).exclude(sender=user).count()
	
	return JsonResponse({
		"status": "success",
		"unread_count": unread_count
	})


# ──────────────────────────────────────────────────────────────
# Admin Dashboard
# ──────────────────────────────────────────────────────────────

@login_required
def admin_dashboard(request):
	guard = _require_admin(request)
	if guard:
		return guard

	stats = _admin_stats()
	recent_properties = Property.objects.select_related("seller").order_by("-created_at")[:3]
	recent_users = User.objects.filter(role__in=[User.Role.BUYER, User.Role.SELLER]).order_by("-date_joined")[:3]
	now = timezone.now()
	current_start = now - timedelta(days=30)
	previous_start = now - timedelta(days=60)
	def trend(current, previous):
		if previous == 0:
			return {"value": 100 if current else 0, "up": bool(current)}
		return {"value": round(((current - previous) / previous) * 100), "up": current >= previous}
	stats["total_users_trend"] = trend(
		User.objects.filter(date_joined__gte=current_start).count(),
		User.objects.filter(date_joined__gte=previous_start, date_joined__lt=current_start).count(),
	)
	stats["buyers_trend"] = trend(
		User.objects.filter(role=User.Role.BUYER, date_joined__gte=current_start).count(),
		User.objects.filter(role=User.Role.BUYER, date_joined__gte=previous_start, date_joined__lt=current_start).count(),
	)
	stats["sellers_trend"] = trend(
		User.objects.filter(role=User.Role.SELLER, date_joined__gte=current_start).count(),
		User.objects.filter(role=User.Role.SELLER, date_joined__gte=previous_start, date_joined__lt=current_start).count(),
	)
	stats["verified_properties_trend"] = trend(
		Property.objects.filter(is_verified=True, created_at__gte=current_start).count(),
		Property.objects.filter(is_verified=True, created_at__gte=previous_start, created_at__lt=current_start).count(),
	)
	stats["pending_properties_trend"] = trend(
		Property.objects.filter(is_verified=False, created_at__gte=current_start).count(),
		Property.objects.filter(is_verified=False, created_at__gte=previous_start, created_at__lt=current_start).count(),
	)
	growth_periods = {
		"all": {"label": "All Time", "unit": "all", "count": None},
		"7d": {"label": "Last 7 Days", "unit": "days", "count": 7},
		"1m": {"label": "Last 1 Month", "unit": "months", "count": 1},
		"3m": {"label": "Last 3 Months", "unit": "months", "count": 3},
		"6m": {"label": "Last 6 Months", "unit": "months", "count": 6},
	}
	growth_period = request.GET.get("growth_period", "6m")
	if growth_period not in growth_periods:
		growth_period = "6m"
	period = growth_periods[growth_period]
	local_now = timezone.localtime()
	user_growth = []

	if period["unit"] == "all":
		user_growth = [{"label": "All Time", "sub_label": "", "count": User.objects.count()}]
	elif period["unit"] == "days":
		start_date = local_now.date() - timedelta(days=period["count"] - 1)
		for offset in range(period["count"]):
			bucket_date = start_date + timedelta(days=offset)
			bucket_start = timezone.make_aware(datetime.combine(bucket_date, datetime.min.time()))
			bucket_end = bucket_start + timedelta(days=1)
			user_growth.append({
				"label": bucket_date.strftime("%a"),
				"sub_label": bucket_date.strftime("%d %b"),
				"count": User.objects.filter(date_joined__gte=bucket_start, date_joined__lt=bucket_end).count(),
			})
	else:
		month_cursor = local_now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
		for offset in range(period["count"] - 1, -1, -1):
			month_index = month_cursor.month - offset - 1
			year = month_cursor.year + month_index // 12
			month = month_index % 12 + 1
			user_growth.append({
				"label": datetime(year, month, 1).strftime("%b"),
				"sub_label": str(year),
				"count": User.objects.filter(date_joined__year=year, date_joined__month=month).count(),
			})

	max_growth = max((item["count"] for item in user_growth), default=0)
	for item in user_growth:
		item["height"] = max(4, round((item["count"] / max_growth) * 150) if max_growth else 4)

	# Use the same selected range for the property donut, so both charts respond
	# consistently to their own dropdowns.
	property_period = request.GET.get("property_period", "1m")
	if property_period not in growth_periods:
		property_period = "1m"
	if growth_periods[property_period]["unit"] == "all":
		property_range = Property.objects.all()
	elif growth_periods[property_period]["unit"] == "days":
		property_start_date = local_now.date() - timedelta(days=6)
		property_start = timezone.make_aware(datetime.combine(property_start_date, datetime.min.time()))
		property_range = Property.objects.filter(created_at__gte=property_start)
	else:
		month_index = local_now.month - growth_periods[property_period]["count"] + 1
		property_year = local_now.year + month_index // 12
		property_month = month_index % 12 + 1
		property_start = timezone.make_aware(datetime(property_year, property_month, 1))
		property_range = Property.objects.filter(created_at__gte=property_start)
	listing_verified = property_range.filter(is_verified=True).count()
	listing_pending = property_range.filter(is_verified=False).count()
	listing_total = listing_verified + listing_pending
	listing_verified_percent = round((listing_verified / listing_total) * 100) if listing_total else 0

	location_period = request.GET.get("location_period", "all")
	if location_period not in growth_periods:
		location_period = "all"
	location_properties = Property.objects.all()
	if growth_periods[location_period]["unit"] == "days":
		location_start_date = local_now.date() - timedelta(days=6)
		location_start = timezone.make_aware(datetime.combine(location_start_date, datetime.min.time()))
		location_properties = location_properties.filter(created_at__gte=location_start)
	elif growth_periods[location_period]["unit"] == "months":
		location_month_index = local_now.month - growth_periods[location_period]["count"] + 1
		location_year = local_now.year + location_month_index // 12
		location_month = location_month_index % 12 + 1
		location_start = timezone.make_aware(datetime(location_year, location_month, 1))
		location_properties = location_properties.filter(created_at__gte=location_start)

	city_aliases = [
		("new delhi", "New Delhi"), ("bengaluru", "Bengaluru"), ("bangalore", "Bengaluru"),
		("ahmedabad", "Ahmedabad"), ("hyderabad", "Hyderabad"), ("mumbai", "Mumbai"),
		("pune", "Pune"), ("chennai", "Chennai"), ("kolkata", "Kolkata"),
		("jaipur", "Jaipur"), ("gurugram", "Gurugram"), ("gurgaon", "Gurugram"),
		("noida", "Noida"), ("delhi", "Delhi"),
	]

	def identify_city(raw_location):
		location_text = (raw_location or "").strip()
		for alias, city_name in city_aliases:
			if re.search(rf"\b{re.escape(alias)}\b", location_text, flags=re.IGNORECASE):
				return city_name
		parts = [part.strip() for part in location_text.split(",") if part.strip()]
		fallback_parts = [part for part in parts if not part.isdigit() and not re.search(r"\d{4,}", part)]
		return fallback_parts[-2] if len(fallback_parts) > 1 else (fallback_parts[0] if fallback_parts else "Unknown")

	location_counts = Counter(
		identify_city(location) for location in location_properties.values_list("location", flat=True)
	)
	top_location_rows = location_counts.most_common(5)
	max_location_count = top_location_rows[0][1] if top_location_rows else 0
	top_locations = [
		{"name": name, "count": count, "width": round((count / max_location_count) * 100) if max_location_count else 0}
		for name, count in top_location_rows
	]
	property_total = stats["verified_properties"] + stats["pending_properties"]
	verified_percent = round((stats["verified_properties"] / property_total) * 100) if property_total else 0

	return render(request, "admin_panel/dashboard.html", {
		"stats": stats,
		"admin_stats": stats,
		"recent_properties": recent_properties,
		"recent_users": recent_users,
		"user_growth": user_growth,
		"growth_period": growth_period,
		"property_period": property_period,
		"listing_verified": listing_verified,
		"listing_pending": listing_pending,
		"listing_verified_percent": listing_verified_percent,
		"location_period": location_period,
		"top_locations": top_locations,
		"rejected_properties": 0,
		"verified_percent": verified_percent,
	})


@login_required
def admin_reports(request):
	guard = _require_admin(request)
	if guard:
		return guard

	period = request.GET.get("period", "all")
	if period not in {"all", "today", "week", "month", "year"}:
		period = "all"
	now = timezone.localtime()
	period_start = None
	if period == "today":
		period_start = timezone.make_aware(datetime.combine(now.date(), datetime.min.time()))
	elif period == "week":
		period_start = timezone.make_aware(datetime.combine(now.date() - timedelta(days=now.weekday()), datetime.min.time()))
	elif period == "month":
		period_start = timezone.make_aware(datetime(now.year, now.month, 1))
	elif period == "year":
		period_start = timezone.make_aware(datetime(now.year, 1, 1))

	properties = Property.objects.all()
	if period_start:
		properties = properties.filter(created_at__gte=period_start)
	users = User.objects.all()
	if period_start:
		users = users.filter(date_joined__gte=period_start)

	property_type_distribution = [
		{"label": label, "count": properties.filter(property_type=value).count()}
		for value, label in Property.PropertyType.choices
	]
	property_type_distribution = [item for item in property_type_distribution if item["count"]]

	location_counts = Counter(properties.values_list("location", flat=True))
	location_distribution = [{"label": name, "count": count} for name, count in location_counts.most_common(6) if name]
	max_location = max((item["count"] for item in location_distribution), default=0)
	for item in location_distribution:
		item["width"] = round(item["count"] * 100 / max_location) if max_location else 0

	listing_activity = []
	activity_days = 7 if period in {"today", "week"} else 30
	activity_start = period_start or (now - timedelta(days=activity_days - 1))
	if period == "all":
		oldest = Property.objects.order_by("created_at").values_list("created_at", flat=True).first()
		activity_start = oldest or activity_start
	activity_start = timezone.localtime(activity_start).date()
	end_date = now.date()
	current_date = activity_start
	while current_date <= end_date:
		listing_activity.append({
			"label": current_date.strftime("%d %b"),
			"count": properties.filter(created_at__date=current_date).count(),
		})
		current_date += timedelta(days=1)
	# Keep the chart compact on datasets spanning a long history.
	if len(listing_activity) > 31:
		listing_activity = listing_activity[-31:]
	max_activity = max((item["count"] for item in listing_activity), default=0)
	for item in listing_activity:
		item["height"] = max(4, round(item["count"] * 130 / max_activity)) if max_activity else 4

	return render(request, "admin_panel/reports.html", {
		"admin_stats": _admin_stats(),
		"period": period,
		"property_overview": {
			"total": properties.count(),
			"available": properties.filter(status=Property.PropertyStatus.AVAILABLE).count(),
			"sold": properties.filter(status=Property.PropertyStatus.SOLD).count(),
			"rented": properties.filter(status=Property.PropertyStatus.RENTED).count(),
			"verified": properties.filter(is_verified=True).count(),
			"pending": properties.filter(is_verified=False).count(),
			"rejected": None,
		},
		"user_overview": {
			"total": users.count(),
			"buyers": users.filter(role=User.Role.BUYER).count(),
			"sellers": users.filter(role=User.Role.SELLER).count(),
			"active": users.filter(is_active=True).count(),
			"inactive": users.filter(is_active=False).count(),
		},
		"property_type_distribution": property_type_distribution,
		"location_distribution": location_distribution,
		"listing_activity": listing_activity,
	})


@login_required
def admin_user_activity(request):
	guard = _require_admin(request)
	if guard:
		return guard
	activities = []
	for user in User.objects.order_by("-date_joined")[:100]:
		activities.append({"user": user, "type": "New Registration", "details": "Account created", "date": user.date_joined, "status": "Completed"})
	for property_obj in Property.objects.select_related("seller").order_by("-created_at")[:100]:
		activities.append({"user": property_obj.seller, "type": "Property Submitted", "details": property_obj.title, "date": property_obj.created_at, "status": "Verified" if property_obj.is_verified else "Pending"})
		if property_obj.updated_at and property_obj.updated_at != property_obj.created_at:
			activities.append({"user": property_obj.seller, "type": "Property Updated", "details": property_obj.title, "date": property_obj.updated_at, "status": property_obj.status.title()})
	for inquiry in InquiryChat.objects.select_related("buyer", "property").order_by("-created_at")[:100]:
		activities.append({"user": inquiry.buyer, "type": "Inquiry Created", "details": inquiry.property.title, "date": inquiry.created_at, "status": "Open"})
	for message in ChatMessage.objects.select_related("sender", "inquiry__property").order_by("-created_at")[:100]:
		activities.append({"user": message.sender, "type": "Contact Activity", "details": message.inquiry.property.title, "date": message.created_at, "status": "Read" if message.read_at else "Unread"})
	activities.sort(key=lambda item: item["date"], reverse=True)
	return render(request, "admin_panel/activity.html", {"admin_stats": _admin_stats(), "activities": activities[:150]})


@login_required
def admin_settings(request):
	guard = _require_admin(request)
	if guard:
		return guard
	platform_settings, _created = PlatformSettings.objects.get_or_create()
	if request.method == "POST":
		form = PlatformSettingsForm(request.POST, instance=platform_settings)
		if form.is_valid():
			form.save()
			django_messages.success(request, "Settings saved successfully.")
			return redirect(f"{reverse('core:admin_settings')}?tab={request.POST.get('active_tab', 'general')}")
	else:
		form = PlatformSettingsForm(instance=platform_settings)
	active_tab = request.GET.get("tab", "general")
	if active_tab not in {"general", "listings", "users", "notifications", "content", "appearance"}:
		active_tab = "general"
	return render(request, "admin_panel/settings.html", {
		"admin_stats": _admin_stats(),
		"form": form,
		"settings": platform_settings,
		"active_tab": active_tab,
		"account_roles": [label for _value, label in User.Role.choices],
		"property_statuses": Property.PropertyStatus.choices,
	})


@login_required
def admin_activity(request):
	return admin_user_activity(request)


# ──────────────────────────────────────────────────────────────
# Admin Users
# ──────────────────────────────────────────────────────────────

@login_required
def admin_users(request):
	guard = _require_admin(request)
	if guard:
		return guard

	qs = User.objects.filter(role__in=[User.Role.BUYER, User.Role.SELLER]).order_by("-date_joined")
	q = request.GET.get("q", "").strip()
	role = request.GET.get("role", "").strip()

	if q:
		qs = qs.filter(Q(username__icontains=q) | Q(email__icontains=q))
	if role in {User.Role.BUYER, User.Role.SELLER}:
		qs = qs.filter(role=role)

	return render(request, "admin_panel/users.html", {
		"users": qs,
		"users_count": qs.count(),
		"admin_stats": _admin_stats(),
	})


@login_required
def admin_user_edit(request, pk):
	guard = _require_admin(request)
	if guard:
		return guard

	user_obj = get_object_or_404(User, pk=pk, role__in=[User.Role.BUYER, User.Role.SELLER])
	if request.method == "POST":
		form = AdminPanelUserForm(request.POST, instance=user_obj)
		if form.is_valid():
			form.save()
			django_messages.success(request, f"Updated user '{user_obj.username}'.")
			return redirect("core:admin_users")
	else:
		form = AdminPanelUserForm(instance=user_obj)

	return render(request, "admin_panel/user_edit.html", {
		"form": form,
		"user_obj": user_obj,
		"admin_stats": _admin_stats(),
	})


@login_required
def admin_user_create(request):
	guard = _require_admin(request)
	if guard:
		return guard

	if request.method == "POST":
		form = AdminPanelUserCreateForm(request.POST)
		if form.is_valid():
			new_user = form.save()
			django_messages.success(request, f"Created user '{new_user.username}'.")
			return redirect("core:admin_users")
	else:
		form = AdminPanelUserCreateForm()

	return render(request, "admin_panel/user_create.html", {
		"form": form,
		"admin_stats": _admin_stats(),
	})


@login_required
def admin_user_delete(request, pk):
	guard = _require_admin(request)
	if guard:
		return guard
	if request.method == "POST":
		user_obj = get_object_or_404(User, pk=pk, role__in=[User.Role.BUYER, User.Role.SELLER])
		if user_obj.id == request.user.id:
			msg = "You cannot delete your own account."
			if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
				return JsonResponse({'success': False, 'message': msg}, status=400)
			django_messages.error(request, msg)
			return redirect("core:admin_users")
		username = user_obj.username
		user_obj.delete()
		msg = f"Deleted user '{username}'."
		if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
			return JsonResponse({'success': True, 'message': msg})
		django_messages.success(request, msg)
	return redirect("core:admin_users")


# ──────────────────────────────────────────────────────────────
# Admin Properties
# ──────────────────────────────────────────────────────────────

@login_required
def admin_properties(request):
	guard = _require_admin(request)
	if guard:
		return guard

	qs = Property.objects.select_related("seller").order_by("-created_at")
	q = request.GET.get("q", "").strip()
	ptype = request.GET.get("type", "").strip()
	status_filter = request.GET.get("status", "").strip()
	verified = request.GET.get("verified", "").strip()

	if q:
		qs = qs.filter(Q(title__icontains=q) | Q(location__icontains=q) | Q(seller__username__icontains=q))
	if ptype:
		qs = qs.filter(property_type=ptype)
	if status_filter == "pending":
		qs = qs.filter(is_verified=False)
	elif status_filter in {choice[0] for choice in Property.PropertyStatus.choices}:
		qs = qs.filter(status=status_filter)
	elif verified == "1":
		qs = qs.filter(is_verified=True)
	elif verified == "0":
		qs = qs.filter(is_verified=False)

	return render(request, "admin_panel/properties.html", {
		"properties": qs,
		"properties_count": qs.count(),
		"property_types": Property.PropertyType.choices,
		"property_statuses": Property.PropertyStatus.choices,
		"admin_stats": _admin_stats(),
	})


@login_required
def admin_property_create(request):
	guard = _require_admin(request)
	if guard:
		return guard

	if request.method == "POST":
		form = AdminPanelPropertyForm(request.POST, request.FILES)
		if form.is_valid():
			prop = form.save()
			django_messages.success(request, f"Created property '{prop.title}'.")
			return redirect("core:admin_properties")
	else:
		form = AdminPanelPropertyForm()

	return render(request, "admin_panel/property_create.html", {
		"form": form,
		"admin_stats": _admin_stats(),
	})


@login_required
def admin_property_edit(request, pk):
	guard = _require_admin(request)
	if guard:
		return guard
	property_obj = get_object_or_404(Property, pk=pk)
	if request.method == "POST":
		form = AdminPanelPropertyForm(request.POST, request.FILES, instance=property_obj)
		if form.is_valid():
			form.save()
			django_messages.success(request, f"Updated property '{property_obj.title}'.")
			return redirect("core:admin_properties")
	else:
		form = AdminPanelPropertyForm(instance=property_obj)
	return render(request, "admin_panel/property_create.html", {
		"form": form,
		"admin_stats": _admin_stats(),
		"edit_mode": True,
		"property_obj": property_obj,
	})


@login_required
def admin_property_detail(request, pk):
	guard = _require_admin(request)
	if guard:
		return guard

	property_obj = get_object_or_404(Property.objects.select_related("seller"), pk=pk)

	return render(request, "admin_panel/property_detail.html", {
		"property": property_obj,
		"status_choices": Property.PropertyStatus.choices,
		"amenities": [item.strip() for item in property_obj.amenities.splitlines() if item.strip()],
		"admin_stats": _admin_stats(),
	})


@login_required
def admin_properties_pending(request):
	guard = _require_admin(request)
	if guard:
		return guard

	query = request.GET.get("q", "").strip()
	property_type = request.GET.get("type", "").strip()
	documents = request.GET.get("documents", "").strip()
	qs = Property.objects.filter(is_verified=False).select_related("seller").order_by("-created_at")
	if query:
		qs = qs.filter(Q(title__icontains=query) | Q(location__icontains=query) | Q(seller__username__icontains=query) | Q(seller__email__icontains=query))
	if property_type:
		qs = qs.filter(property_type=property_type)
	if documents == "available":
		qs = qs.exclude(documents="")
	elif documents == "missing":
		qs = qs.filter(Q(documents="") | Q(documents__isnull=True))
	return render(request, "admin_panel/properties_pending.html", {
		"properties": qs,
		"pending_count": qs.count(),
		"documents_count": qs.exclude(documents="").exclude(documents__isnull=True).count(),
		"property_types": Property.PropertyType.choices,
		"admin_stats": _admin_stats(),
	})


@login_required
def admin_property_approve(request, pk):
	guard = _require_admin(request)
	if guard:
		return guard
	if request.method == "POST":
		prop = get_object_or_404(Property, pk=pk)
		prop.is_verified = True
		prop.save(update_fields=["is_verified"])
		msg = f"'{prop.title}' has been approved."
		if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
			return JsonResponse({'success': True, 'message': msg})
		django_messages.success(request, msg)
	return redirect(request.META.get("HTTP_REFERER", "core:admin_properties"))


@login_required
def admin_property_unapprove(request, pk):
	guard = _require_admin(request)
	if guard:
		return guard
	if request.method == "POST":
		prop = get_object_or_404(Property, pk=pk)
		prop.is_verified = False
		prop.save(update_fields=["is_verified"])
		msg = f"Verification revoked for '{prop.title}'."
		if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
			return JsonResponse({'success': True, 'message': msg})
		django_messages.success(request, msg)
	return redirect(request.META.get("HTTP_REFERER", "core:admin_properties"))


@login_required
def admin_property_delete(request, pk):
	guard = _require_admin(request)
	if guard:
		return guard
	if request.method == "POST":
		prop = get_object_or_404(Property, pk=pk)
		title = prop.title
		prop.delete()
		msg = f"Deleted property '{title}'."
		if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
			return JsonResponse({'success': True, 'message': msg})
		django_messages.success(request, msg)
	return redirect("core:admin_properties")


@login_required
def admin_property_update_status(request, pk):
	"""Allow admins to update property status"""
	guard = _require_admin(request)
	if guard:
		return guard
	
	if request.method == "POST":
		prop = get_object_or_404(Property, pk=pk)
		new_status = request.POST.get("status", "").strip()

		if new_status not in [choice[0] for choice in Property.PropertyStatus.choices]:
			msg = "Invalid status."
			if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
				return JsonResponse({'success': False, 'message': msg}, status=400)
			django_messages.error(request, msg)
			return redirect(request.META.get("HTTP_REFERER", f"core:admin_property_detail pk={pk}"))

		prop.status = new_status
		prop.save(update_fields=["status"])
		msg = f"Property status updated to '{prop.get_status_display()}'."
		if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
			return JsonResponse({'success': True, 'message': msg})
		django_messages.success(request, msg)
	
	return redirect(request.META.get("HTTP_REFERER", "core:admin_properties"))


# ──────────────────────────────────────────────────────────────
# Admin Inquiries
# ──────────────────────────────────────────────────────────────

@login_required
def admin_inquiries(request):
	guard = _require_admin(request)
	if guard:
		return guard

	qs = InquiryChat.objects.select_related("buyer", "property", "property__seller").annotate(
		msg_count=Count("messages")
	).order_by("-created_at")

	q = request.GET.get("q", "").strip()
	if q:
		qs = qs.filter(
			Q(buyer__username__icontains=q) |
			Q(property__title__icontains=q) |
			Q(message__icontains=q)
		)

	return render(request, "admin_panel/inquiries.html", {
		"page_obj": _paginate(qs, request),
		"admin_stats": _admin_stats(),
	})


@login_required
def admin_inquiry_detail(request, pk):
	guard = _require_admin(request)
	if guard:
		return guard

	inquiry = get_object_or_404(
		InquiryChat.objects.select_related("buyer", "property", "property__seller"),
		pk=pk,
	)
	messages_qs = inquiry.messages.select_related("sender").order_by("created_at")

	return render(request, "admin_panel/inquiry_detail.html", {
		"inquiry": inquiry,
		"messages": messages_qs,
		"admin_stats": _admin_stats(),
	})


@login_required
def admin_inquiry_delete(request, pk):
	guard = _require_admin(request)
	if guard:
		return guard
	if request.method == "POST":
		inquiry = get_object_or_404(InquiryChat, pk=pk)
		inquiry.delete()
		msg = "Deleted inquiry thread."
		if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
			return JsonResponse({'success': True, 'message': msg})
		django_messages.success(request, msg)
	return redirect("core:admin_inquiries")


# ──────────────────────────────────────────────────────────────
# Admin Chat Messages
# ──────────────────────────────────────────────────────────────

@login_required
def admin_messages(request):
	guard = _require_admin(request)
	if guard:
		return guard

	qs = ChatMessage.objects.select_related(
		"sender", "inquiry", "inquiry__property"
	).order_by("-created_at")

	q = request.GET.get("q", "").strip()
	read_filter = request.GET.get("read", "").strip()

	if q:
		qs = qs.filter(Q(sender__username__icontains=q) | Q(message__icontains=q))
	if read_filter == "unread":
		qs = qs.filter(read_at__isnull=True)
	elif read_filter == "read":
		qs = qs.filter(read_at__isnull=False)

	return render(request, "admin_panel/messages.html", {
		"page_obj": _paginate(qs, request),
		"admin_stats": _admin_stats(),
	})


@login_required
def admin_message_delete(request, pk):
	guard = _require_admin(request)
	if guard:
		return guard
	if request.method == "POST":
		msg = get_object_or_404(ChatMessage, pk=pk)
		msg.delete()
		message = "Deleted chat message."
		if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
			return JsonResponse({'success': True, 'message': message})
		django_messages.success(request, message)
	return redirect("core:admin_messages")


# ──────────────────────────────────────────────────────────────
# Admin Saved Properties
# ──────────────────────────────────────────────────────────────

@login_required
def admin_saved_properties(request):
	guard = _require_admin(request)
	if guard:
		return guard

	qs = SavedProperty.objects.select_related(
		"buyer", "property", "property__seller"
	).filter(
		buyer__role=User.Role.BUYER
	).order_by("-created_at")

	q = request.GET.get("q", "").strip()
	if q:
		qs = qs.filter(
			Q(buyer__username__icontains=q) |
			Q(property__title__icontains=q)
		)

	return render(request, "admin_panel/saved_properties.html", {
		"page_obj": _paginate(qs, request),
		"admin_stats": _admin_stats(),
	})


@login_required
def admin_saved_property_detail(request, pk):
	guard = _require_admin(request)
	if guard:
		return guard

	saved_item = get_object_or_404(
		SavedProperty.objects.select_related("buyer", "property", "property__seller"),
		buyer__role=User.Role.BUYER,
		pk=pk,
	)

	return render(request, "admin_panel/saved_property_detail.html", {
		"saved_item": saved_item,
		"admin_stats": _admin_stats(),
	})


@login_required
def admin_saved_property_delete(request, pk):
	guard = _require_admin(request)
	if guard:
		return guard
	if request.method == "POST":
		saved = get_object_or_404(SavedProperty, pk=pk)
		saved.delete()
		msg = "Deleted saved property entry."
		if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
			return JsonResponse({'success': True, 'message': msg})
		django_messages.success(request, msg)
	return redirect("core:admin_saved_properties")





def about_view(request):
	stats = {
		"total_properties": Property.objects.count(),
		"active_listings": Property.objects.filter(status=Property.PropertyStatus.AVAILABLE).count(),
		"registered_users": User.objects.count(),
		"trusted_sellers": User.objects.filter(role=User.Role.SELLER).count(),
	}
	return render(request, "core/about.html", {"stats": stats})


def contact_view(request):
	if request.method == "POST":
		is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
		
		full_name = request.POST.get("full_name", "").strip()
		email = request.POST.get("email", "").strip()
		subject = request.POST.get("subject", "").strip()
		message = request.POST.get("message", "").strip()

		if full_name and email and subject and message:
			ContactMessage.objects.create(
				full_name=full_name,
				email=email,
				subject=subject,
				message=message,
			)
			
			if is_ajax:
				return JsonResponse({
					'success': True,
					'message': 'Thank you for contacting Urban Hunt! Your message has been sent successfully.'
				})
			
			django_messages.success(
				request,
				"Thank you for contacting Urban Hunt! Your message has been sent successfully."
			)
			return redirect("core:contact")
		else:
			error_msg = "Please fill out all required fields before submitting your message."
			
			if is_ajax:
				return JsonResponse({
					'success': False,
					'message': error_msg
				}, status=400)
			
			django_messages.error(request, error_msg)

	return render(request, "core/contact.html")


def privacy_policy_view(request):
	return render(request, "core/legal.html")


def terms_of_service_view(request):
	return render(request, "core/legal.html")


def legal_view(request):
	return render(request, "core/legal.html")
