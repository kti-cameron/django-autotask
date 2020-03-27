# Generated by Django 2.1.11 on 2020-03-25 13:35

from django.db import migrations, models
import django.db.models.deletion
import django_extensions.db.fields


class Migration(migrations.Migration):

    dependencies = [
        ('djautotask', '0054_merge_20200323_1511'),
    ]

    operations = [
        migrations.CreateModel(
            name='AccountType',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created', django_extensions.db.fields.CreationDateTimeField(auto_now_add=True, verbose_name='created')),
                ('modified', django_extensions.db.fields.ModificationDateTimeField(auto_now=True, verbose_name='modified')),
                ('label', models.CharField(blank=True, max_length=50, null=True)),
                ('is_default_value', models.BooleanField(default=False)),
                ('sort_order', models.PositiveSmallIntegerField(blank=True, null=True)),
                ('is_active', models.BooleanField(default=False)),
                ('is_system', models.BooleanField(default=False)),
            ],
            options={
                'abstract': False,
                'ordering': ('label',),
            },
        ),
        migrations.AddField(
            model_name='account',
            name='type',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='djautotask.AccountType'),
        ),
    ]
