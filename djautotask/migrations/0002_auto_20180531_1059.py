# Generated by Django 2.0.5 on 2018-05-31 10:59

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('djautotask', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='ticketnote',
            name='ticket',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='djautotask.Ticket'),
        ),
    ]
