from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('home', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='InvoiceSeries',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('datetime', models.DateTimeField(auto_now_add=True)),
                ('lastUpdatedOn', models.DateTimeField(auto_now=True)),
                ('isDeleted', models.BooleanField(default=False)),
                ('module', models.CharField(choices=[('sales', 'Sales'), ('cash_counter', 'Cash Counter')], max_length=40)),
                ('name', models.CharField(blank=True, max_length=200, null=True)),
                ('seriesCode', models.CharField(max_length=40)),
                ('startNumber', models.PositiveIntegerField(default=1)),
                ('endNumber', models.PositiveIntegerField(default=9999)),
                ('nextNumber', models.PositiveIntegerField(default=1)),
                ('paddingLength', models.PositiveIntegerField(default=4)),
                ('isActive', models.BooleanField(default=True)),
                ('isDefault', models.BooleanField(default=False)),
                ('tenant', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='invoice_series', to='home.tenant')),
            ],
            options={
                'verbose_name_plural': 'Invoice Series',
                'indexes': [
                    models.Index(fields=['tenant', 'module', 'isActive'], name='invser_tenant_active_idx'),
                    models.Index(fields=['tenant', 'module', 'isDefault'], name='invser_tenant_default_idx'),
                ],
                'constraints': [
                    models.UniqueConstraint(fields=('tenant', 'module', 'seriesCode'), name='unique_invoice_series_per_module'),
                ],
            },
        ),
        migrations.AddField(
            model_name='cashcounter',
            name='invoiceSequenceNumber',
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='cashcounter',
            name='invoiceSeries',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='cash_counters', to='home.invoiceseries'),
        ),
        migrations.AddField(
            model_name='sales',
            name='invoiceSequenceNumber',
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='sales',
            name='invoiceSeries',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='sales', to='home.invoiceseries'),
        ),
    ]
