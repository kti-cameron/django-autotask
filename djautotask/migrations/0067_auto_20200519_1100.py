# Generated by Django 2.1.14 on 2020-05-19 11:00

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('djautotask', '0066_auto_20200512_1420'),
    ]

    operations = [
        migrations.AddField(
            model_name='ticket',
            name='service_level_agreement',
            field=models.IntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='ticket',
            name='service_level_agreement_has_been_met',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='ticket',
            name='service_level_agreement_paused_next_event_hours',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=9, null=True),
        ),
    ]
