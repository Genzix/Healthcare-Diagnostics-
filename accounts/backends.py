from django.contrib.auth.backends import ModelBackend
from django.contrib.auth import get_user_model
from .services import normalize_phone_number

UserModel = get_user_model()


class EmailOrPhoneModelBackend(ModelBackend):
    """
    Custom Authentication Backend allowing login to Django Admin and the platform
    using either Email address or Phone number.
    """
    def authenticate(self, request, username=None, password=None, **kwargs):
        if username is None:
            username = kwargs.get(UserModel.USERNAME_FIELD) or kwargs.get('email')

        if not username or not password:
            return None

        username_str = str(username).strip()

        # 1. Primary lookup: Case-insensitive email match
        user = UserModel.objects.filter(email__iexact=username_str).first()

        # 2. Secondary lookup: Normalized phone number
        if not user:
            clean_phone = normalize_phone_number(username_str)
            user = UserModel.objects.filter(phone_number=clean_phone).first()

        # 3. Fallback: Direct phone number string
        if not user:
            user = UserModel.objects.filter(phone_number=username_str).first()

        if user and user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None

