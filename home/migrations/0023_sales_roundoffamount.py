from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('home', '0022_salesline_discountpercent'),
    ]

    operations = [
        migrations.AddField(
            model_name='sales',
            name='roundOffAmount',
            field=models.FloatField(default=0.0),
        ),
    ]
