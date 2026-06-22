from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('home', '0023_sales_roundoffamount'),
    ]

    operations = [
        migrations.AddField(
            model_name='cashcounter',
            name='buyerBillingAddress',
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='cashcounter',
            name='buyerGstNumber',
            field=models.CharField(blank=True, max_length=20, null=True),
        ),
        migrations.AddField(
            model_name='cashcounter',
            name='buyerLegalName',
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.AddField(
            model_name='cashcounter',
            name='gstAmount',
            field=models.FloatField(default=0.0),
        ),
        migrations.AddField(
            model_name='cashcounter',
            name='invoiceKind',
            field=models.CharField(choices=[('b2c', 'B2C'), ('b2b', 'B2B')], default='b2c', max_length=10),
        ),
        migrations.AddField(
            model_name='cashcounter',
            name='placeOfSupply',
            field=models.CharField(blank=True, max_length=120, null=True),
        ),
        migrations.AddField(
            model_name='cashcounter',
            name='roundOffAmount',
            field=models.FloatField(default=0.0),
        ),
        migrations.AddField(
            model_name='cashcounter',
            name='taxableAmount',
            field=models.FloatField(default=0.0),
        ),
        migrations.AddField(
            model_name='cashcounterline',
            name='discountPercent',
            field=models.FloatField(default=0.0),
        ),
        migrations.AddField(
            model_name='cashcounterline',
            name='gstAmount',
            field=models.FloatField(default=0.0),
        ),
        migrations.AddField(
            model_name='cashcounterline',
            name='gstRate',
            field=models.FloatField(default=0.0),
        ),
        migrations.AddField(
            model_name='cashcounterline',
            name='hsnCode',
            field=models.CharField(blank=True, max_length=40, null=True),
        ),
        migrations.AddField(
            model_name='cashcounterline',
            name='taxableAmount',
            field=models.FloatField(default=0.0),
        ),
    ]
