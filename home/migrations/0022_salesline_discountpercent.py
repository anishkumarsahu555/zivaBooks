from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('home', '0021_invoiceseries_invoicenature_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='salesline',
            name='discountPercent',
            field=models.FloatField(default=0.0),
        ),
    ]
