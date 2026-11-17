import getpass
from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth import get_user_model
from django.core.validators import validate_email
from django.core.exceptions import ValidationError
from accounts.models import phone_validator

User = get_user_model()


class Command(BaseCommand):
    help = 'Create a superuser/admin account using Email (Phone number is optional and kept empty if omitted).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--email',
            dest='email',
            default=None,
            help='Specifies the email address for the superuser.',
        )
        parser.add_argument(
            '--phone',
            '--phone_number',
            dest='phone_number',
            default=None,
            help='Specifies the phone number for the superuser (optional, left empty if omitted).',
        )
        parser.add_argument(
            '--password',
            dest='password',
            default=None,
            help='Specifies the password for the superuser (optional, prompted if omitted).',
        )
        parser.add_argument(
            '--first-name',
            dest='first_name',
            default='Admin',
            help='Specifies the first name for the superuser.',
        )
        parser.add_argument(
            '--last-name',
            dest='last_name',
            default='User',
            help='Specifies the last name for the superuser.',
        )
        parser.add_argument(
            '--noinput',
            '--no-input',
            action='store_false',
            dest='interactive',
            default=True,
            help='Tells Django to NOT prompt the user for input of any kind.',
        )
        parser.add_argument(
            '--database',
            default='default',
            help='Specifies the database to use. Default is "default".',
        )

    def handle(self, *args, **options):
        database = options.get('database', 'default')
        interactive = options.get('interactive', True)
        email = options.get('email')
        phone = options.get('phone_number')
        password = options.get('password')
        first_name = options.get('first_name', 'Admin')
        last_name = options.get('last_name', 'User')

        if interactive:
            # 1. Prompt for Email (Required)
            while not email:
                input_email = input('Email: ').strip()
                if not input_email:
                    self.stderr.write("Error: Email is required.")
                    continue
                try:
                    validate_email(input_email)
                    email = input_email.lower()
                except ValidationError:
                    self.stderr.write("Error: Enter a valid email address.")

            # Check if user with this email already exists
            existing_user = User.objects.using(database).filter(email__iexact=email).first()
            if existing_user:
                self.stdout.write(self.style.WARNING(
                    f"User with email '{email}' already exists (Phone: {existing_user.phone_number or '(empty)'})."
                ))
                confirm = input("Promote this existing user to Superuser/Admin? [Y/n]: ").strip().lower()
                if confirm in ('', 'y', 'yes'):
                    new_pw = getpass.getpass('New Password (press Enter to keep existing password): ')
                    if new_pw.strip():
                        existing_user.set_password(new_pw)
                    existing_user.role = 'ADMIN'
                    existing_user.is_staff = True
                    existing_user.is_superuser = True
                    existing_user.is_phone_verified = True
                    existing_user.save(using=database)
                    self.stdout.write(self.style.SUCCESS(
                        f"Existing user '{email}' was successfully promoted to Superuser (Admin)."
                    ))
                    return
                else:
                    self.stdout.write("Operation cancelled.")
                    return

            # 2. Prompt for Phone number (Optional - Enter marks as empty/None)
            input_phone = input('Phone number (optional, press Enter to leave empty): ').strip()
            if input_phone:
                clean_phone = input_phone.replace(" ", "").replace("-", "")
                try:
                    phone_validator(clean_phone)
                    if User.objects.using(database).filter(phone_number=clean_phone).exists():
                        self.stderr.write(f"Warning: Phone number '{clean_phone}' is already in use. Leaving phone number empty.")
                        phone = None
                    else:
                        phone = clean_phone
                except ValidationError:
                    self.stderr.write("Warning: Invalid phone number format. Leaving phone number empty.")
                    phone = None
            else:
                phone = None

            # 3. Prompt for Password
            while not password:
                p1 = getpass.getpass('Password: ')
                if not p1:
                    self.stderr.write("Error: Password cannot be blank.")
                    continue
                p2 = getpass.getpass('Password (again): ')
                if p1 != p2:
                    self.stderr.write("Error: Passwords do not match. Please try again.")
                    continue
                password = p1
        else:
            # Non-interactive mode
            if not email:
                raise CommandError("You must provide --email when using --noinput.")
            try:
                validate_email(email)
                email = email.lower()
            except ValidationError:
                raise CommandError(f"Invalid email: {email}")

            if phone:
                clean_phone = phone.replace(" ", "").replace("-", "")
                phone = clean_phone
            else:
                phone = None

            if not password:
                import os
                password = os.environ.get('DJANGO_SUPERUSER_PASSWORD')
                if not password:
                    raise CommandError("You must provide --password or set DJANGO_SUPERUSER_PASSWORD environment variable with --noinput.")

        # Check existing user in non-interactive
        existing = User.objects.using(database).filter(email__iexact=email).first()
        if existing:
            existing.role = 'ADMIN'
            existing.is_staff = True
            existing.is_superuser = True
            existing.is_phone_verified = True
            if password:
                existing.set_password(password)
            existing.save(using=database)
            self.stdout.write(self.style.SUCCESS(
                f"Existing user '{email}' updated to Superuser successfully."
            ))
            return

        # Create new superuser
        user = User.objects.db_manager(database).create_superuser(
            phone_number=phone,
            email=email,
            password=password,
            first_name=first_name,
            last_name=last_name,
            role='ADMIN',
            is_staff=True,
            is_superuser=True,
            is_phone_verified=True,
            is_active=True,
        )

        phone_display = user.phone_number if user.phone_number else "(empty)"
        self.stdout.write(self.style.SUCCESS(
            f"Superuser created successfully.\n"
            f"  Email:        {user.email}\n"
            f"  Phone number: {phone_display}\n"
            f"  Role:         {user.role}\n"
            f"  Staff:        {user.is_staff}\n"
            f"  Superuser:    {user.is_superuser}"
        ))
