from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('home', '0025_cashcounter_linked_sales_return'),
    ]

    operations = [
        migrations.AddField(
            model_name='salesreturnvoucher',
            name='cashCounter',
            field=models.ForeignKey(blank=True, null=True, on_delete=models.SET_NULL, related_name='return_vouchers', to='home.cashcounter'),
        ),
    ]
