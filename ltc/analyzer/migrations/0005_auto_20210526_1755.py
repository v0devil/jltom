# Generated by Django 2.2.20 on 2021-05-26 15:55

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('analyzer', '0004_auto_20210526_1744'),
    ]

    operations = [
        migrations.RenameModel(
            old_name='ReportVariable',
            new_name='GraphiteVariable',
        ),
        migrations.RenameField(
            model_name='graphitevariable',
            old_name='params',
            new_name='query',
        ),
    ]
