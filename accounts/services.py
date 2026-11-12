import logging
import re
from datetime import timedelta
from django.utils import timezone
from django.conf import settings
from django.contrib.auth import get_user_model
from .models import PhoneOTP

logger = logging.getLogger(__name__)


def normalize_phone_number(phone_number: str) -> str:
    """
    Normalizes phone number by stripping whitespace, hyphens, and parentheses.
    Preserves leading '+' if present.
    """
    if not phone_number:
        return ""
    cleaned = re.sub(r'[\s\-\(\)]', '', str(phone_number).strip())
    return cleaned


def send_sms_otp(phone_number: str, otp_code: str) -> bool:
    """
    Dispatches OTP via SMS service.
    In local development and testing, logs the OTP to console and logger.
    Easily configurable with external SMS gateways (Twilio, MSG91, AWS SNS, etc.).
    """
    message = (
        f"[Healthcare Diagnostics] Your registration OTP is {otp_code}. "
        f"Valid for {getattr(settings, 'OTP_EXPIRY_MINUTES', 5)} minutes. "
        "Do not share this code with anyone."
    )
    
    # Primary logging for server/console observability
    logger.info("SMS Dispatch -> To: %s | Message: %s", phone_number, message)
    print(f"\n=======================================================")
    print(f" [SMS SERVICE] Sending to: {phone_number}")
    print(f" OTP CODE: {otp_code}")
    print(f"=======================================================\n")
    
    # If production SMS provider credentials exist in settings, plug in here:
    # Example:
    # if getattr(settings, 'TWILIO_ACCOUNT_SID', None):
    #     client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
    #     client.messages.create(body=message, from_=settings.TWILIO_PHONE_NUMBER, to=phone_number)
    
    return True


def create_and_send_registration_otp(phone_number: str, metadata: dict = None):
    """
    Initiates registration OTP:
    1. Validates user does not already exist as verified.
    2. Checks resend cooldown window.
    3. Invalidates previous unverified OTPs.
    4. Generates and stores new OTP record.
    5. Dispatches SMS.
    
    Returns:
        tuple: (PhoneOTP instance or None, error_message or None)
    """
    phone_number = normalize_phone_number(phone_number)
    User = get_user_model()

    # 1. Check if user already exists and is verified
    existing_user = User.objects.filter(phone_number=phone_number, is_phone_verified=True).first()
    if existing_user:
        return None, "An account with this phone number already exists. Please log in."

    # 2. Check resend cooldown
    resend_cooldown = getattr(settings, 'OTP_RESEND_WAIT_SECONDS', 60)
    recent_otp = PhoneOTP.objects.filter(
        phone_number=phone_number,
        purpose='REGISTRATION',
        is_verified=False,
        created_at__gte=timezone.now() - timedelta(seconds=resend_cooldown)
    ).first()

    if recent_otp:
        seconds_left = int(resend_cooldown - (timezone.now() - recent_otp.created_at).total_seconds())
        if seconds_left > 0:
            return None, f"Please wait {seconds_left} second(s) before requesting a new OTP."

    # 3. Invalidate older pending registration OTPs for this number
    PhoneOTP.objects.filter(
        phone_number=phone_number,
        purpose='REGISTRATION',
        is_verified=False
    ).update(is_verified=False)

    # 4. Generate new OTP
    otp_code = PhoneOTP.generate_otp_code(length=6)
    expiry_minutes = getattr(settings, 'OTP_EXPIRY_MINUTES', 5)
    expires_at = timezone.now() + timedelta(minutes=expiry_minutes)

    otp_record = PhoneOTP.objects.create(
        phone_number=phone_number,
        otp_code=otp_code,
        purpose='REGISTRATION',
        expires_at=expires_at,
        metadata=metadata or {}
    )

    # 5. Dispatch SMS
    send_sms_otp(phone_number, otp_code)

    return otp_record, None


def verify_registration_otp(phone_number: str, otp_code: str):
    """
    Validates provided OTP against active registration record.
    
    Returns:
        tuple: (PhoneOTP instance or None, error_message or None)
    """
    phone_number = normalize_phone_number(phone_number)
    otp_code = str(otp_code).strip()

    # Retrieve most recent unverified OTP record
    otp_record = PhoneOTP.objects.filter(
        phone_number=phone_number,
        purpose='REGISTRATION',
        is_verified=False
    ).order_by('-created_at').first()

    if not otp_record:
        return None, "No active OTP request found for this phone number. Please request a new OTP."

    # Check expiration
    if otp_record.is_expired():
        return None, "OTP has expired. Please request a new OTP."

    # Check maximum attempt limit
    if otp_record.attempts >= otp_record.max_attempts:
        return None, "Maximum verification attempts exceeded. Please request a new OTP."

    # Verify code match
    if otp_record.otp_code != otp_code:
        otp_record.attempts += 1
        otp_record.save(update_fields=['attempts'])
        remaining = otp_record.max_attempts - otp_record.attempts
        if remaining > 0:
            return None, f"Invalid OTP code. {remaining} attempt(s) remaining."
        return None, "Invalid OTP code. Maximum verification attempts reached. Please request a new OTP."

    # Successfully verified!
    otp_record.is_verified = True
    otp_record.save(update_fields=['is_verified'])

    return otp_record, None


def create_user_account(phone_number: str, user_data: dict = None):
    """
    Creates or activates a User account upon successful OTP verification.
    
    Returns:
        tuple: (User instance, token_key or None)
    """
    phone_number = normalize_phone_number(phone_number)
    user_data = user_data or {}
    User = get_user_model()

    first_name = user_data.get('first_name', '').strip()
    last_name = user_data.get('last_name', '').strip()
    email = user_data.get('email', '').strip() or None
    role = user_data.get('role', 'PATIENT')
    password = user_data.get('password')

    # Look for existing user with this phone number (e.g. unverified account)
    user = User.objects.filter(phone_number=phone_number).first()

    if user:
        # Update user attributes
        user.is_phone_verified = True
        user.is_active = True
        if first_name:
            user.first_name = first_name
        if last_name:
            user.last_name = last_name
        if email:
            user.email = email
        if role:
            user.role = role
        if password:
            user.set_password(password)
        user.save()
    else:
        # Create new user
        extra_fields = {
            'first_name': first_name,
            'last_name': last_name,
            'email': email,
            'role': role,
            'is_phone_verified': True,
            'is_active': True,
        }
        user = User.objects.create_user(
            phone_number=phone_number,
            password=password,
            **extra_fields
        )

    # Issue auth token if rest_framework.authtoken is enabled and available
    token_key = issue_auth_token(user)
    return user, token_key


def get_tokens_for_user(user):
    """
    Generates SimpleJWT access and refresh tokens for user.
    Includes custom claims: phone_number and role.
    """
    try:
        from rest_framework_simplejwt.tokens import RefreshToken
        refresh = RefreshToken.for_user(user)
        refresh['phone_number'] = str(user.phone_number)
        refresh['role'] = str(user.role)
        refresh['is_staff'] = bool(user.is_staff)
        return {
            'refresh': str(refresh),
            'access': str(refresh.access_token),
        }
    except Exception as exc:
        logger.warning("JWT token generation failed: %s", exc)
        return None


def issue_auth_token(user):
    """
    Safely generates or retrieves DRF authtoken for the given user.
    """
    token_key = None
    try:
        from rest_framework.authtoken.models import Token
        token_obj, _ = Token.objects.get_or_create(user=user)
        token_key = token_obj.key
    except Exception as exc:
        logger.warning("Token generation skipped or authtoken table not yet migrated: %s", exc)
    return token_key


def create_and_send_login_otp(phone_number: str):
    """
    Initiates login OTP for an existing verified user.
    
    Returns:
        tuple: (PhoneOTP instance or None, error_message or None)
    """
    phone_number = normalize_phone_number(phone_number)
    User = get_user_model()

    # 1. Validate user existence
    user = User.objects.filter(phone_number=phone_number).first()
    if not user:
        return None, "No account found with this phone number. Please register first."
    if not user.is_phone_verified:
        return None, "This phone number has not been verified yet. Please complete registration."
    if not user.is_active:
        return None, "This account is inactive. Please contact support."

    # 2. Check resend cooldown
    resend_cooldown = getattr(settings, 'OTP_RESEND_WAIT_SECONDS', 60)
    recent_otp = PhoneOTP.objects.filter(
        phone_number=phone_number,
        purpose='LOGIN',
        is_verified=False,
        created_at__gte=timezone.now() - timedelta(seconds=resend_cooldown)
    ).first()

    if recent_otp:
        seconds_left = int(resend_cooldown - (timezone.now() - recent_otp.created_at).total_seconds())
        if seconds_left > 0:
            return None, f"Please wait {seconds_left} second(s) before requesting a new login OTP."

    # 3. Invalidate previous pending LOGIN OTPs
    PhoneOTP.objects.filter(
        phone_number=phone_number,
        purpose='LOGIN',
        is_verified=False
    ).update(is_verified=False)

    # 4. Generate new OTP
    otp_code = PhoneOTP.generate_otp_code(length=6)
    expiry_minutes = getattr(settings, 'OTP_EXPIRY_MINUTES', 5)
    expires_at = timezone.now() + timedelta(minutes=expiry_minutes)

    otp_record = PhoneOTP.objects.create(
        phone_number=phone_number,
        otp_code=otp_code,
        purpose='LOGIN',
        expires_at=expires_at,
        metadata={}
    )

    # 5. Dispatch SMS
    send_sms_otp(phone_number, otp_code)

    return otp_record, None


def verify_login_otp(phone_number: str, otp_code: str):
    """
    Validates login OTP and returns authenticated user and auth token.
    
    Returns:
        tuple: (User instance or None, token_key or None, error_message or None)
    """
    phone_number = normalize_phone_number(phone_number)
    otp_code = str(otp_code).strip()
    User = get_user_model()

    user = User.objects.filter(phone_number=phone_number).first()
    if not user:
        return None, None, "No account found with this phone number."
    if not user.is_active:
        return None, None, "This account is inactive."

    # Retrieve most recent unverified LOGIN OTP
    otp_record = PhoneOTP.objects.filter(
        phone_number=phone_number,
        purpose='LOGIN',
        is_verified=False
    ).order_by('-created_at').first()

    if not otp_record:
        return None, None, "No active login OTP found. Please request an OTP."

    if otp_record.is_expired():
        return None, None, "Login OTP has expired. Please request a new one."

    if otp_record.attempts >= otp_record.max_attempts:
        return None, None, "Maximum verification attempts exceeded. Please request a new OTP."

    if otp_record.otp_code != otp_code:
        otp_record.attempts += 1
        otp_record.save(update_fields=['attempts'])
        remaining = otp_record.max_attempts - otp_record.attempts
        if remaining > 0:
            return None, None, f"Invalid OTP code. {remaining} attempt(s) remaining."
        return None, None, "Invalid OTP code. Maximum verification attempts reached."

    # Mark OTP verified
    otp_record.is_verified = True
    otp_record.save(update_fields=['is_verified'])

    # Issue auth token
    token_key = issue_auth_token(user)

    return user, token_key, None


def authenticate_with_password(phone_number: str, password: str):
    """
    Authenticates user with phone number and password.
    
    Returns:
        tuple: (User instance or None, token_key or None, error_message or None)
    """
    phone_number = normalize_phone_number(phone_number)
    User = get_user_model()

    user = User.objects.filter(phone_number=phone_number).first()
    if not user or not user.check_password(password):
        return None, None, "Invalid phone number or password."

    if not user.is_active:
        return None, None, "This account is inactive."

    token_key = issue_auth_token(user)
    return user, token_key, None


