import secrets
from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth import get_user_model

User = get_user_model()


class Command(BaseCommand):
    help = 'Creates an Admin account with email and password (sets role=ADMIN, is_staff=True, is_superuser=True)'

    def add_arguments(self, parser):
        parser.add_argument('--email', type=str, help='Admin email address')
        parser.add_argument('--password', type=str, help='Admin password')
        parser.add_argument('--phone', type=str, help='Admin phone number (optional)')
        parser.add_argument('--first-name', type=str, default='Admin', help='Admin first name')
        parser.add_argument('--last-name', type=str, default='User', help='Admin last name')

    def handle(self, *args, **options):
        email = options.get('email')
        password = options.get('password')
        phone = options.get('phone')
        first_name = options.get('first_name', 'Admin')
        last_name = options.get('last_name', 'User')

        if not email:
            email = input('Enter Admin Email: ').strip()
        if not email:
            raise CommandError('Email is required for admin.')

        # If a user with this email already exists, promote them to ADMIN
        existing = User.objects.filter(email__iexact=email).first()
        if existing:
            existing.role = 'ADMIN'
            existing.is_staff = True
            existing.is_superuser = True
            if password:
                existing.set_password(password)
            existing.save()
            self.stdout.write(self.style.SUCCESS(
                f"Existing user '{email}' (phone: {existing.phone_number}) successfully updated to ADMIN."
            ))
            return

        if not password:
            import getpass
            password = getpass.getpass('Enter Admin Password: ')
        if not password:
            raise CommandError('Password is required.')

        if not phone:
            # Generate a unique phone number placeholder for this admin
            phone = f"+9190000{secrets.randbelow(90000) + 10000}"

        admin = User.objects.create_user(
            phone_number=phone,
            password=password,
            email=email,
            first_name=first_name,
            last_name=last_name,
            role='ADMIN',
            is_staff=True,
            is_superuser=True,
            is_phone_verified=True,
        )

        self.stdout.write(self.style.SUCCESS(
            f"Admin account created successfully!\n"
            f"  Email: {admin.email}\n"
            f"  Phone: {admin.phone_number}\n"
            f"  Role:  {admin.role}\n"
            f"  Staff: {admin.is_staff}\n"
            f"You can now log in at POST /api/admin/login/ using email '{admin.email}' and your password."
        ))

