"""
Family member phone verification.

Flow
----
1. ``send_family_phone_otp``   – the account holder asks us to text a code to the
   relative's number. Guarded by format checks, ownership/duplicate checks, a
   resend cooldown and hourly send caps (per number and per account).
2. ``verify_family_phone_otp`` – the holder types the code the relative received.
   Attempts are counted atomically (row lock) and compared in constant time. On
   success we hand back a short-lived, signed *verification token* bound to
   (account, phone, otp record).
3. Creating/updating a family member with a phone requires that token
   (``check_verification_token``); it is single-use (``consume_verification_token``),
   so a number cannot be swapped between verifying and saving, and a token cannot
   be replayed for another member or another account.

A phone is mandatory unless the member is a child / minor (see ``phone_required_for``).
"""
import hmac
import logging
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from django.conf import settings
from django.core import signing
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from .models import FamilyMember, PhoneOTP
from .services import normalize_phone_number, send_sms_otp

logger = logging.getLogger(__name__)

PURPOSE = 'FAMILY_MEMBER'
TOKEN_SALT = 'accounts.family-phone-verification'

E164_IN = re.compile(r'^\+91[6-9]\d{9}$')
E164_ANY = re.compile(r'^\+[1-9]\d{7,14}$')


def _setting(name, default):
    return getattr(settings, name, default)


def otp_expiry_minutes():
    return _setting('OTP_EXPIRY_MINUTES', 5)


def resend_wait_seconds():
    return _setting('OTP_RESEND_WAIT_SECONDS', 60)


def token_ttl_seconds():
    return _setting('FAMILY_PHONE_TOKEN_TTL_SECONDS', 15 * 60)


def max_sends_per_phone_hour():
    return _setting('FAMILY_OTP_MAX_PER_PHONE_PER_HOUR', 5)


def max_sends_per_account_hour():
    return _setting('FAMILY_OTP_MAX_PER_ACCOUNT_PER_HOUR', 15)


# ─────────────────────────── phone helpers ───────────────────────────

def canonical_phone(raw) -> str:
    """
    Canonical storage form. Bare 10-digit numbers (and 0/91-prefixed ones) are
    treated as Indian mobiles: '98765 43210' / '098765…' / '9198765…' → '+919876543210'.
    Returns '' for blank input; the result may still be invalid (see ``is_valid_phone``).
    """
    s = normalize_phone_number(raw or '')
    if not s:
        return ''
    if s.startswith('00'):
        s = '+' + s[2:]
    if s.startswith('+'):
        return '+' + re.sub(r'\D', '', s[1:])
    digits = re.sub(r'\D', '', s)
    if len(digits) == 11 and digits.startswith('0'):
        digits = digits[1:]
    if len(digits) == 10:
        return '+91' + digits
    if len(digits) == 12 and digits.startswith('91'):
        return '+' + digits
    return '+' + digits


def is_valid_phone(phone: str) -> bool:
    if phone.startswith('+91'):
        return bool(E164_IN.match(phone))
    return bool(E164_ANY.match(phone))


def same_phone(a, b) -> bool:
    """Format-insensitive equality ('9876543210' == '+91 98765-43210')."""
    ca, cb = canonical_phone(a), canonical_phone(b)
    return bool(ca) and ca == cb


def mask_phone(phone: str) -> str:
    p = canonical_phone(phone)
    return f"{p[:-10]} {p[-10:-8]}xxxxxx{p[-2:]}" if len(p) >= 10 else p


def _age_on(dob, today=None):
    if not dob:
        return None
    if isinstance(dob, str):
        try:
            dob = date.fromisoformat(dob)
        except ValueError:
            return None
    today = today or timezone.localdate()
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


def phone_required_for(relationship, date_of_birth=None) -> bool:
    """
    Adults must have a verified mobile. Exempt: children (by relationship) and
    anyone under 18 by date of birth (e.g. a minor sibling).
    """
    if (relationship or '').upper() in FamilyMember.PHONE_OPTIONAL_RELATIONSHIPS:
        return False
    age = _age_on(date_of_birth)
    if age is not None and age < FamilyMember.ADULT_AGE:
        return False
    return True


# ─────────────────────────── errors ───────────────────────────

@dataclass
class FamilyPhoneError(Exception):
    message: str
    code: str = 'invalid'
    status: int = 400
    extra: dict = field(default_factory=dict)

    def __str__(self):
        return self.message


def validate_phone_for_owner(owner, raw_phone, member_id=None) -> str:
    """Shared checks for OTP send and member save. Returns the canonical phone."""
    phone = canonical_phone(raw_phone)
    if not phone or not is_valid_phone(phone):
        raise FamilyPhoneError('Enter a valid 10-digit mobile number.', 'invalid_phone')

    if owner.phone_number and same_phone(owner.phone_number, phone):
        raise FamilyPhoneError(
            'This is your own number. Add the family member’s own mobile number.',
            'own_phone',
        )

    others = FamilyMember.objects.filter(user=owner).exclude(contact_number='')
    if member_id:
        others = others.exclude(pk=member_id)
    for m in others.only('id', 'name', 'contact_number'):
        if same_phone(m.contact_number, phone):
            raise FamilyPhoneError(
                f'This number is already used for {m.name} in your family.',
                'duplicate_phone',
                extra={'member_id': m.pk},
            )
    return phone


# ─────────────────────────── send ───────────────────────────

def _owner_otps(owner):
    return PhoneOTP.objects.filter(purpose=PURPOSE, metadata__owner_id=owner.pk)


def send_family_phone_otp(owner, raw_phone, member_id=None, member_name=''):
    """
    Returns (PhoneOTP, info dict). Raises FamilyPhoneError.
    """
    if member_id and not FamilyMember.objects.filter(pk=member_id, user=owner).exists():
        raise FamilyPhoneError('Family member not found.', 'not_found', status=404)

    phone = validate_phone_for_owner(owner, raw_phone, member_id)
    now = timezone.now()

    with transaction.atomic():
        # Serialize concurrent sends for this owner (row lock on the user).
        type(owner).objects.select_for_update().filter(pk=owner.pk).first()

        last = _owner_otps(owner).filter(phone_number=phone).order_by('-created_at').first()
        if last and not last.is_verified:
            wait = int(resend_wait_seconds() - (now - last.created_at).total_seconds())
            if wait > 0:
                raise FamilyPhoneError(
                    f'Please wait {wait} second(s) before requesting a new code.',
                    'resend_cooldown', status=429, extra={'retry_after_seconds': wait},
                )

        hour_ago = now - timedelta(hours=1)
        recent = _owner_otps(owner).filter(created_at__gte=hour_ago)
        if recent.filter(phone_number=phone).count() >= max_sends_per_phone_hour():
            raise FamilyPhoneError(
                'Too many codes sent to this number. Please try again in an hour.',
                'rate_limited', status=429, extra={'retry_after_seconds': 3600},
            )
        if recent.count() >= max_sends_per_account_hour():
            raise FamilyPhoneError(
                'Too many verification requests from your account. Please try again later.',
                'rate_limited', status=429, extra={'retry_after_seconds': 3600},
            )

        # Retire any still-pending codes for this number so only the newest works.
        _owner_otps(owner).filter(phone_number=phone, is_verified=False, expires_at__gt=now).update(expires_at=now)

        otp = PhoneOTP.objects.create(
            phone_number=phone,
            otp_code=PhoneOTP.generate_otp_code(6),
            purpose=PURPOSE,
            max_attempts=_setting('OTP_MAX_ATTEMPTS', 5),
            expires_at=now + timedelta(minutes=otp_expiry_minutes()),
            metadata={'owner_id': owner.pk, 'member_id': member_id},
        )

    owner_name = owner.get_full_name() or 'A family member'
    who = f' {member_name.strip()},' if member_name and member_name.strip() else ''
    send_sms_otp(phone, otp.otp_code, message=(
        f"Hi{who} {owner_name} is adding you to their family on 1MinDoc. "
        f"Share this code with them to confirm your number: {otp.otp_code}. "
        f"Valid for {otp_expiry_minutes()} minutes."
    ))

    return otp, {
        'phone_number': phone,
        'masked_phone': mask_phone(phone),
        'otp_expiry_seconds': otp_expiry_minutes() * 60,
        'resend_after_seconds': resend_wait_seconds(),
    }


# ─────────────────────────── verify ───────────────────────────

def verify_family_phone_otp(owner, raw_phone, code):
    """
    Returns (token, info dict). Raises FamilyPhoneError.
    """
    phone = canonical_phone(raw_phone)
    code = str(code or '').strip()
    if not phone or not is_valid_phone(phone):
        raise FamilyPhoneError('Enter a valid 10-digit mobile number.', 'invalid_phone')
    if not re.fullmatch(r'\d{4,6}', code):
        raise FamilyPhoneError('Enter the 6-digit code.', 'invalid_otp')

    with transaction.atomic():
        otp = (_owner_otps(owner).select_for_update()
               .filter(phone_number=phone, is_verified=False)
               .order_by('-created_at').first())
        if not otp:
            raise FamilyPhoneError('No code was requested for this number. Tap “Send code”.', 'no_otp')
        if otp.is_expired():
            raise FamilyPhoneError('This code has expired. Request a new one.', 'otp_expired')
        if otp.attempts >= otp.max_attempts:
            raise FamilyPhoneError('Too many wrong attempts. Request a new code.', 'otp_locked')

        matched = hmac.compare_digest(otp.otp_code, code)
        if matched:
            otp.is_verified = True
            otp.save(update_fields=['is_verified'])
        else:
            # Committed before raising — raising inside atomic() would roll it back.
            PhoneOTP.objects.filter(pk=otp.pk).update(attempts=F('attempts') + 1)
            remaining = otp.max_attempts - otp.attempts - 1

    if not matched:
        if remaining <= 0:
            raise FamilyPhoneError('Too many wrong attempts. Request a new code.', 'otp_locked')
        raise FamilyPhoneError(
            f'Incorrect code. {remaining} attempt{"s" if remaining != 1 else ""} left.',
            'otp_mismatch', extra={'attempts_remaining': remaining},
        )

    token = signing.dumps({'o': owner.pk, 'p': phone, 'n': otp.pk}, salt=TOKEN_SALT, compress=True)
    return token, {'phone_number': phone, 'expires_in_seconds': token_ttl_seconds()}


# ─────────────────────────── token ───────────────────────────

def check_verification_token(owner, phone, token):
    """
    Validates a verification token for (owner, phone). Returns the OTP record id
    to consume after the save succeeds. Raises FamilyPhoneError.
    """
    if not token:
        raise FamilyPhoneError('Verify this number with the OTP before saving.', 'phone_unverified')
    try:
        data = signing.loads(token, salt=TOKEN_SALT, max_age=token_ttl_seconds())
    except signing.SignatureExpired:
        raise FamilyPhoneError('Verification expired. Please verify the number again.', 'token_expired')
    except signing.BadSignature:
        raise FamilyPhoneError('Verification is invalid. Please verify the number again.', 'token_invalid')

    if data.get('o') != owner.pk or not same_phone(data.get('p'), phone):
        raise FamilyPhoneError('This number was not the one verified. Please verify it again.', 'token_mismatch')

    used = PhoneOTP.objects.filter(
        pk=data.get('n'), purpose=PURPOSE, is_verified=True, metadata__token_used=True,
    ).exists()
    if used:
        raise FamilyPhoneError('This verification was already used. Please verify the number again.', 'token_used')
    return data.get('n')


def consume_verification_token(otp_id):
    """Marks the OTP's token as spent (call inside the save transaction)."""
    otp = PhoneOTP.objects.select_for_update().filter(pk=otp_id).first()
    if not otp:
        return
    if (otp.metadata or {}).get('token_used'):
        raise FamilyPhoneError('This verification was already used. Please verify the number again.', 'token_used')
    otp.metadata = {**(otp.metadata or {}), 'token_used': True, 'used_at': timezone.now().isoformat()}
    otp.save(update_fields=['metadata'])
