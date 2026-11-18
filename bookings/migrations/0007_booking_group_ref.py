from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('bookings', '0006_booking_collection_location'),
    ]

    operations = [
        migrations.AddField(
            model_name='booking',
            name='group_ref',
            field=models.CharField(blank=True, db_index=True, default='', max_length=30),
        ),
    ]
