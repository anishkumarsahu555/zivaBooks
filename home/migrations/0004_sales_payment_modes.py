from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('home', '0003_alter_auditlog_action'),
    ]

    operations = [
        migrations.AddField(
            model_name='sales',
            name='bankID',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='sales_entries', to='home.bank'),
        ),
        migrations.AddField(
            model_name='sales',
            name='mixCashAmount',
            field=models.FloatField(default=0.0),
        ),
        migrations.AddField(
            model_name='sales',
            name='mixOnlineAmount',
            field=models.FloatField(default=0.0),
        ),
        migrations.AddField(
            model_name='sales',
            name='modeOfPayment',
            field=models.CharField(choices=[('Cash', 'Cash'), ('Credit', 'Credit'), ('Online', 'Online'), ('Mix', 'Mix')], default='Cash', max_length=50),
        ),
    ]
