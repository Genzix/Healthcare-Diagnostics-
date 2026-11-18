"""
Moves each user's single profile address (User.address/city/pincode) into the new
address book as their default HOME address. No coordinates are known, so the
address is flagged LEGACY and the app asks the user to pin it on the map when
they next edit it. Idempotent: users who already have addresses are skipped.
"""
import re

from django.db import migrations

PIN = re.compile(r'\b([1-9]\d{5})\b')


def forwards(apps, schema_editor):
    User = apps.get_model('accounts', 'User')
    UserAddress = apps.get_model('accounts', 'UserAddress')

    users = User.objects.exclude(address='').exclude(address__isnull=True).exclude(addresses__isnull=False)
    batch = []
    for u in users.iterator():
        raw = (u.address or '').strip()
        pincode = re.sub(r'\D', '', u.pincode or '')
        if not re.fullmatch(r'[1-9]\d{5}', pincode):
            m = PIN.search(raw)
            pincode = m.group(1) if m else ''
        city = (u.city or '').strip()
        if not pincode or not city:
            continue  # not enough to be useful; the user will add one in the app

        parts = [p.strip() for p in raw.split(',') if p.strip()]
        parts = [p for p in parts if p != pincode and p.lower() != city.lower() and not PIN.fullmatch(p)]
        house = (parts[0] if parts else raw)[:120]
        area = ', '.join(parts[1:])[:200]
        batch.append(UserAddress(
            user_id=u.pk, label='HOME', house=house, area=area, city=city[:100], pincode=pincode,
            location_source='LEGACY', is_default=True,
        ))
    UserAddress.objects.bulk_create(batch, batch_size=500)


class Migration(migrations.Migration):
    dependencies = [
        ('accounts', '0006_user_addresses'),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
