(function (window, $) {
    function num(value) {
        var parsed = parseFloat(value);
        return isNaN(parsed) ? 0 : parsed;
    }

    function itemOption(item, allowNonGst) {
        var sku = item.SKU ? ' [' + item.SKU + ']' : '';
        var gstRate = Number(item.GSTRate || 0).toFixed(2).replace(/\.00$/, '');
        var availableStock = Number(item.GSTStock || 0).toFixed(2).replace(/\.00$/, '');
        if (allowNonGst) {
            availableStock += ' / ' + Number(item.NonGSTStock || 0).toFixed(2).replace(/\.00$/, '');
        }
        return '<option value="' + item.ID + '" data-default-bucket="' + (item.DefaultStockBucket || 'gst') + '" data-price="' + (item.DefaultSalesPrice || 0) + '" data-gst-rate="' + (item.GSTRate || 0) + '" data-hsn="' + (item.HSN || '') + '" data-unit-name="' + (item.UnitName || '') + '" data-unit-allows-decimal="' + (item.UnitAllowsDecimal ? 1 : 0) + '" data-gst-stock="' + (item.GSTStock || 0) + '" data-non-gst-stock="' + (item.NonGSTStock || 0) + '" data-requires-batch="' + (item.RequiresBatchTracking ? 1 : 0) + '">' + item.Name + sku + ' | GST ' + gstRate + '% | Avl ' + availableStock + '</option>';
    }

    function rowHtml(optionsHtml, allowNonGst, row) {
        row = row || {};
        var bucketOptions = '<option value="gst">GST</option>';
        if (allowNonGst) {
            bucketOptions += '<option value="non_gst">Non GST</option>';
        }
        return '' +
            '<div class="sales-gst-row sales-line-card">' +
            '<div class="sales-line-card-header">' +
            '<button type="button" class="ui circular youtube icon button sales-remove" data-tooltip="Remove Row" data-position="left center" data-variation="mini" style="font-size:10px;"><i class="trash alternate icon"></i></button>' +
            '</div>' +
            '<input type="hidden" class="sales-batch-id" value="' + (row.batchId || '') + '">' +
            '<div class="sales-line-grid">' +
            '<div class="sales-span-5"><div class="field"><label>Item</label><select class="ui fluid search selection dropdown sales-item"><option value="">Select item</option>' + optionsHtml + '</select><div class="sales-inline-meta"><span class="sales-unit-badge">Unit: <span class="sales-unit-name">-</span></span><span class="sales-stock-hint">GST: 0' + (allowNonGst ? ' | Non-GST: 0' : '') + '</span></div></div></div>' +
            '<div class="sales-span-2"><div class="field"><label>Bucket</label><select class="ui fluid selection dropdown sales-bucket">' + bucketOptions + '</select></div></div>' +
            '<div class="sales-span-2"><div class="field"><label>Qty</label><div class="ui fluid input"><input type="number" class="sales-qty" min="0" step="0.01" value="' + (row.quantity || '') + '"></div></div></div>' +
            '<div class="sales-span-3"><div class="field"><label>Rate</label><div class="ui fluid input"><input type="number" class="sales-rate" min="0" step="0.01" value="' + (row.unitPrice || '') + '"></div></div></div>' +
            '<div class="sales-span-1"><div class="field"><label>Disc %</label><div class="ui fluid input"><input type="number" class="sales-discount-percent" min="0" max="100" step="0.01" value="' + (row.discountPercent || '') + '"></div></div></div>' +
            '<input type="hidden" class="sales-batch-number" value="' + (row.batchNumber || '') + '">' +
            '<input type="hidden" class="sales-expiry-date" value="' + (row.expiryDate || '') + '">' +
            '<div class="sales-span-5"><div class="field sales-batch-field"><label>Batch / FEFO</label><div class="sales-batch-panel"><div class="sales-batch-select-row"><select class="ui fluid search selection dropdown sales-batch-select"><option value="">Auto FEFO</option></select><button type="button" class="ui teal basic icon button sales-batch-trace" data-tooltip="View Batch Trace" data-position="top center" data-variation="mini"><i class="history icon"></i></button></div><div class="sales-batch-meta"><span class="sales-batch-pill muted">Auto FEFO</span><span class="sales-batch-meta-text">Earliest eligible batch will be picked on save.</span></div></div></div></div>' +
            '<div class="sales-span-2"><div class="field"><label>Taxable</label><div class="ui fluid input"><input type="number" class="sales-taxable" min="0" step="0.01" value="' + (row.taxableAmount || '') + '" readonly></div></div></div>' +
            '<div class="sales-span-1"><div class="field"><label>GST %</label><div class="ui fluid input"><input type="number" class="sales-gst-rate" min="0" step="0.01" value="' + (row.gstRate || '') + '"></div></div></div>' +
            '<div class="sales-span-2"><div class="field"><label>GST Amt</label><div class="ui fluid input"><input type="number" class="sales-gst-amount" min="0" step="0.01" value="' + (row.gstAmount || '') + '" readonly></div></div></div>' +
            '<div class="sales-span-2"><div class="field"><label>Total</label><div class="ui fluid input"><input type="number" class="sales-total" min="0" step="0.01" value="' + (row.lineTotal || '') + '" readonly></div></div></div>' +
            '<div class="sales-span-12"><div class="field sales-remark-input"><label>Remark</label><div class="ui fluid input"><input type="text" class="sales-remark" maxlength="120" value="' + (row.remark || '') + '" placeholder="Optional"></div></div></div>' +
            '</div>' +
            '<div class="sales-line-summary">' +
            '<div class="sales-line-summary-pills">' +
            '<span class="sales-summary-pill">Taxable ₹ <span class="sales-inline-taxable">' + (num(row.taxableAmount || 0).toFixed(2)) + '</span></span>' +
            '<span class="sales-summary-pill">GST ₹ <span class="sales-inline-gst">' + (num(row.gstAmount || 0).toFixed(2)) + '</span></span>' +
            '<span class="sales-summary-pill">Total ₹ <span class="sales-inline-total">' + (num(row.lineTotal || 0).toFixed(2)) + '</span></span>' +
            '</div>' +
            '</div>' +
            '</div>';
    }

    function applySelectedDefaults($row, options) {
        options = options || {};
        var $selected = selectedItemOption($row);
        if (!$selected.length) {
            return;
        }
        var allowNonGst = $row.closest('.sales-gst-box').data('allowNonGst') === 1 || $row.closest('.sales-gst-box').attr('data-allow-non-gst') === '1';
        var forceItemDefaults = !!options.forceItemDefaults;
        if (forceItemDefaults || !$row.find('.sales-rate').val()) {
            $row.find('.sales-rate').val($selected.data('price') || 0);
        }
        if (forceItemDefaults || !$row.find('.sales-gst-rate').val()) {
            $row.find('.sales-gst-rate').val($selected.data('gst-rate') || 0);
        }
        var preferredBucket = choosePreferredBucket($selected, dropdownValue($row.find('.sales-bucket')), allowNonGst);
        if (preferredBucket && preferredBucket !== dropdownValue($row.find('.sales-bucket'))) {
            try {
                $row.find('.sales-bucket').dropdown('set selected', preferredBucket);
            } catch (e) {
                $row.find('.sales-bucket').val(preferredBucket);
            }
        }
        if (forceItemDefaults) {
            $row.find('.sales-taxable').val('');
            $row.find('.sales-gst-amount').val('');
            $row.find('.sales-total').val('');
        }
        updateHint($row);
    }

    function dropdownValue($element) {
        if (!$element.length) {
            return '';
        }
        var dropdownValue = '';
        try {
            if (typeof $element.dropdown === 'function') {
                dropdownValue = $element.dropdown('get value');
            }
        } catch (e) {}
        if (dropdownValue !== undefined && dropdownValue !== null && String(dropdownValue).trim() !== '') {
            return String(dropdownValue).trim();
        }
        var rawValue = $element.val();
        if (rawValue !== undefined && rawValue !== null && String(rawValue).trim() !== '') {
            return String(rawValue).trim();
        }
        var hiddenValue = $element.closest('.ui.dropdown').find('input[type="hidden"]').val();
        return String(hiddenValue || '').trim();
    }

    function selectedItemOption($row) {
        var itemId = dropdownValue($row.find('.sales-item'));
        if (!itemId) {
            return $();
        }
        return $row.find('.sales-item option[value="' + itemId + '"]');
    }

    function choosePreferredBucket($selected, currentBucket, allowNonGst) {
        var gstStock = num($selected.data('gst-stock'));
        var nonGstStock = num($selected.data('non-gst-stock'));
        var defaultBucket = String($selected.data('default-bucket') || 'gst');
        var bucket = String(currentBucket || '').trim();
        if (!allowNonGst) {
            return 'gst';
        }
        if (!bucket) {
            if (defaultBucket === 'non_gst' && nonGstStock > 0) {
                return 'non_gst';
            }
            if (defaultBucket === 'gst' && gstStock > 0) {
                return 'gst';
            }
            if (gstStock > 0 && nonGstStock <= 0) {
                return 'gst';
            }
            if (nonGstStock > 0 && gstStock <= 0) {
                return 'non_gst';
            }
            return defaultBucket;
        }
        if (bucket === 'non_gst' && nonGstStock <= 0 && gstStock > 0) {
            return 'gst';
        }
        if (bucket === 'gst' && gstStock <= 0 && nonGstStock > 0) {
            return 'non_gst';
        }
        return bucket;
    }

    function rowPayload($row) {
        var itemId = dropdownValue($row.find('.sales-item'));
        var qty = num($row.find('.sales-qty').val());
        var allowNonGst = $row.closest('.sales-gst-box').data('allowNonGst') === 1 || $row.closest('.sales-gst-box').attr('data-allow-non-gst') === '1';
        var bucket = allowNonGst ? (dropdownValue($row.find('.sales-bucket')) || 'gst') : 'gst';
        var nonGstMode = bucket === 'non_gst';
        if (!itemId || qty <= 0) {
            return null;
        }
        return {
            itemId: itemId,
            batchId: dropdownValue($row.find('.sales-batch-select')) || $row.find('.sales-batch-id').val() || '',
            batchNumber: $row.find('.sales-batch-number').val() || '',
            expiryDate: $row.find('.sales-expiry-date').val() || '',
            stockBucket: bucket,
            quantity: qty,
            unitPrice: num($row.find('.sales-rate').val()),
            discountPercent: num($row.find('.sales-discount-percent').val()),
            taxableAmount: num($row.find('.sales-taxable').val()),
            gstRate: nonGstMode ? 0 : num($row.find('.sales-gst-rate').val()),
            gstAmount: nonGstMode ? 0 : num($row.find('.sales-gst-amount').val()),
            lineTotal: num($row.find('.sales-total').val()),
            remark: $row.find('.sales-remark').val() || ''
        };
    }

    function updateHint($row) {
        var $selected = selectedItemOption($row);
        var gstStock = Number($selected.data('gst-stock') || 0);
        var nonGstStock = Number($selected.data('non-gst-stock') || 0);
        var unitName = $selected.data('unit-name') || '-';
        var requiresBatch = String($selected.data('requires-batch') || '0') === '1';
        var allowNonGst = $row.closest('.sales-gst-box').data('allowNonGst') === 1 || $row.closest('.sales-gst-box').attr('data-allow-non-gst') === '1';
        var hint = 'GST: ' + gstStock;
        if (allowNonGst) {
            hint += ' | Non-GST: ' + nonGstStock;
        }
        $row.find('.sales-unit-name').text(unitName);
        $row.find('.sales-stock-hint').text(hint + (requiresBatch ? ' | Batch required' : ''));
        updateBatchMeta($row);
    }

    function updateBatchMeta($row) {
        var $selectedItem = selectedItemOption($row);
        var requiresBatch = String($selectedItem.data('requires-batch') || '0') === '1';
        var batchId = dropdownValue($row.find('.sales-batch-select')) || $row.find('.sales-batch-id').val() || '';
        var $selectedBatch = batchId ? $row.find('.sales-batch-select option[value="' + batchId + '"]') : $();
        var $firstBatch = $row.find('.sales-batch-select option').filter(function () { return String($(this).val() || '').trim() !== ''; }).first();
        var $pill = $row.find('.sales-batch-pill');
        var $text = $row.find('.sales-batch-meta-text');
        var $trace = $row.find('.sales-batch-trace');

        $trace.toggleClass('disabled', !batchId);
        if (!$selectedItem.length) {
            $pill.attr('class', 'sales-batch-pill muted').text('No item');
            $text.text('Select an item to load eligible batches.');
            return;
        }
        if (batchId && $selectedBatch.length) {
            $pill.attr('class', 'sales-batch-pill selected').text($selectedBatch.data('batch-number') || ('Batch #' + batchId));
            $text.text('Exp ' + ($selectedBatch.data('expiry-date') || '-') + ' | Available ' + ($selectedBatch.data('quantity-on-hand') || 0));
            return;
        }
        if ($firstBatch.length) {
            $pill.attr('class', 'sales-batch-pill fefo').text('Auto FEFO');
            $text.text('Will pick ' + ($firstBatch.data('batch-number') || 'earliest batch') + ' | Exp ' + ($firstBatch.data('expiry-date') || '-') + ' | Available ' + ($firstBatch.data('quantity-on-hand') || 0));
            return;
        }
        if (requiresBatch) {
            $pill.attr('class', 'sales-batch-pill warning').text('Batch required');
            $text.text('No eligible batch stock is available for this item and bucket.');
            return;
        }
        $pill.attr('class', 'sales-batch-pill muted').text('No batch');
        $text.text('This item does not require batch selection.');
    }

    function resetBatchSelection($row) {
        var $select = $row.find('.sales-batch-select');
        $row.find('.sales-batch-id').val('');
        $row.find('.sales-batch-number').val('');
        $row.find('.sales-expiry-date').val('');
        try {
            $select.dropdown('clear');
        } catch (e) {
            $select.val('');
        }
        updateBatchMeta($row);
    }

    function warehouseIdForRow($row) {
        var config = $row.closest('.sales-gst-box').data('salesLineConfig') || {};
        if (typeof config.warehouseSelector === 'function') {
            return config.warehouseSelector($row) || '';
        }
        if (config.warehouseSelector) {
            return $(config.warehouseSelector).val() || '';
        }
        return '';
    }

    function loadBatchOptions($row) {
        var $box = $row.closest('.sales-gst-box');
        var config = $box.data('salesLineConfig') || {};
        if (!config.batchUrl) {
            return;
        }
        var itemId = dropdownValue($row.find('.sales-item'));
        if (!itemId) {
            return;
        }
        $.get(config.batchUrl, {
            itemId: itemId,
            stockBucket: dropdownValue($row.find('.sales-bucket')) || 'gst',
            warehouseId: warehouseIdForRow($row)
        }, function (response) {
            var selectedBatchId = $row.find('.sales-batch-id').val() || '';
            var html = '<option value="">Auto FEFO</option>';
            (response.data || []).forEach(function (batch, index) {
                var label = (index === 0 ? 'FEFO | ' : '') + (batch.BatchNumber || ('Batch #' + batch.ID)) + ' | Exp ' + (batch.ExpiryDate || '-') + ' | Qty ' + (batch.QuantityOnHand || 0) + ' | MRP ' + (batch.MRP || 0);
                html += '<option value="' + batch.ID + '" data-batch-number="' + (batch.BatchNumber || '') + '" data-expiry-date="' + (batch.ExpiryDate || '') + '" data-quantity-on-hand="' + (batch.QuantityOnHand || 0) + '">' + label + '</option>';
            });
            var $select = $row.find('.sales-batch-select');
            $select.html(html);
            try {
                $select.dropdown('refresh');
                if (selectedBatchId) {
                    $select.dropdown('set selected', selectedBatchId);
                }
            } catch (e) {
                $select.val(selectedBatchId);
            }
            updateBatchMeta($row);
        });
    }

    function batchLedgerUrl(config) {
        if (config.batchLedgerUrl) {
            return config.batchLedgerUrl;
        }
        return String(config.batchUrl || '').replace('list_inventory_batches_api', 'list_inventory_batch_ledger_api');
    }

    function ensureBatchTraceModal() {
        if ($('#salesBatchTraceModal').length) {
            return;
        }
        $('body').append(
            '<div class="ui large modal" id="salesBatchTraceModal">' +
            '<i class="close icon"></i>' +
            '<div class="header">Batch Trace</div>' +
            '<div class="content">' +
            '<div class="ui tiny info message" id="salesBatchTraceSummary"></div>' +
            '<div class="ui stackable two column grid">' +
            '<div class="column"><h5 class="ui header">Warehouse Stock</h5><div class="ui relaxed divided list" id="salesBatchTraceWarehouseList"></div></div>' +
            '<div class="column"><h5 class="ui header">Purchase Sources</h5><div class="ui relaxed divided list" id="salesBatchTraceSourceList"></div></div>' +
            '</div>' +
            '<table class="ui unstackable tiny celled very compact table"><thead><tr><th>Date</th><th>Source</th><th>Warehouse</th><th>Qty</th><th>Balance</th><th>Reference</th></tr></thead><tbody id="salesBatchTraceLedgerBody"></tbody></table>' +
            '</div><div class="actions"><div class="ui cancel button">Close</div></div></div>'
        );
    }

    function renderTraceList(rows, selector, emptyText, renderer) {
        var $target = $(selector);
        $target.html('');
        if (!rows || !rows.length) {
            $target.html('<div class="item"><div class="content"><div class="description">' + emptyText + '</div></div></div>');
            return;
        }
        rows.forEach(function (row) { $target.append(renderer(row)); });
    }

    function showBatchTrace($row) {
        var $box = $row.closest('.sales-gst-box');
        var config = $box.data('salesLineConfig') || {};
        var batchId = dropdownValue($row.find('.sales-batch-select')) || $row.find('.sales-batch-id').val() || '';
        var url = batchLedgerUrl(config);
        if (!batchId) {
            $('body').toast({class: 'orange', message: 'Select a batch first to view trace.'});
            return;
        }
        if (!url) {
            $('body').toast({class: 'error', message: 'Batch trace endpoint is unavailable.'});
            return;
        }
        $.get(url, {batchId: batchId}, function (response) {
            if (response.message !== 'success') {
                $('body').toast({class: 'error', message: response.error || 'Unable to load batch trace.'});
                return;
            }
            ensureBatchTraceModal();
            var data = response.data || {};
            var daysText = data.DaysToExpiry === null || data.DaysToExpiry === undefined ? '-' : data.DaysToExpiry + ' days';
            $('#salesBatchTraceSummary').html(
                '<strong>' + (data.ItemName || '-') + '</strong>' +
                ' | Batch: ' + (data.BatchNumber || '-') +
                ' | Status: ' + (data.Status || '-') +
                ' | Mfg: ' + (data.ManufactureDate || '-') +
                ' | Expiry: ' + (data.ExpiryDate || '-') +
                ' | Days: ' + daysText +
                ' | Qty: ' + (data.QuantityOnHand || 0) +
                ' | Rate: ' + (data.PurchaseRate || 0) +
                ' | MRP: ' + (data.MRP || 0)
            );
            renderTraceList(data.WarehouseStocks || [], '#salesBatchTraceWarehouseList', 'No warehouse stock currently available.', function (row) {
                return '<div class="item"><div class="content"><div class="header">' + (row.WarehouseName || '-') + '</div><div class="description">' + (row.StockBucket || '-') + ' | Qty: ' + (row.QuantityOnHand || 0) + '</div></div></div>';
            });
            renderTraceList(data.PurchaseSources || [], '#salesBatchTraceSourceList', 'No purchase source linked yet.', function (row) {
                return '<div class="item"><div class="content"><div class="header">' + (row.PurchaseDisplayID || row.InvoiceNumber || '-') + '</div><div class="description">' + (row.InvoiceDate || '-') + ' | ' + (row.Party || '-') + '</div><div class="description">Qty: ' + (row.Quantity || 0) + ' | Rate: ' + (row.UnitCost || 0) + ' | Total: ' + (row.LineTotal || 0) + '</div></div></div>';
            });
            $('#salesBatchTraceLedgerBody').html('');
            (data.Rows || []).forEach(function (row) {
                var positive = Number(row.QuantityDelta || 0) >= 0;
                $('#salesBatchTraceLedgerBody').append(
                    '<tr><td>' + (row.EntryDate || '-') + '</td><td>' + (row.SourceType || '-') + '</td><td>' + (row.WarehouseName || '-') + '</td><td style="font-weight:700;color:' + (positive ? '#166534' : '#b91c1c') + ';">' + (positive ? '+' : '') + (row.QuantityDelta || 0) + '</td><td>' + (row.RunningBalance || 0) + '</td><td>' + (row.ReferenceNumber || '-') + '<div class="sales-stock-hint">' + (row.Remark || '-') + '</div></td></tr>'
                );
            });
            if (!(data.Rows || []).length) {
                $('#salesBatchTraceLedgerBody').html('<tr><td colspan="6" class="center aligned">No movement found.</td></tr>');
            }
            $('#salesBatchTraceModal').modal('show');
        });
    }

    function applyQuantityStep($row) {
        var $selected = selectedItemOption($row);
        var allowsDecimal = String($selected.data('unit-allows-decimal') || '0') === '1';
        var $qty = $row.find('.sales-qty');
        $qty.attr('step', allowsDecimal ? '0.01' : '1');
        $qty.attr('placeholder', allowsDecimal ? '0.00' : '0');
        $qty.attr('data-allows-decimal', allowsDecimal ? '1' : '0');
        if (!allowsDecimal) {
            var currentValue = String($qty.val() || '').trim();
            if (currentValue && currentValue.indexOf('.') >= 0) {
                $qty.val(String(Math.trunc(num(currentValue))));
            }
        }
    }

    function validateRows($box) {
        var result = {valid: true, message: ''};
        $box.find('.sales-line-list .sales-gst-row').each(function () {
            if (!result.valid) {
                return;
            }
            var $row = $(this);
            var $selectedItem = selectedItemOption($row);
            var itemName = $selectedItem.text() || 'selected item';
            var itemId = dropdownValue($row.find('.sales-item'));
            var qty = num($row.find('.sales-qty').val());
            if (!itemId || qty <= 0) {
                return;
            }
            var batchId = dropdownValue($row.find('.sales-batch-select')) || $row.find('.sales-batch-id').val() || '';
            if (batchId) {
                var $selectedBatch = $row.find('.sales-batch-select option[value="' + batchId + '"]');
                var available = num($selectedBatch.data('quantity-on-hand'));
                if (available > 0 && qty > available) {
                    result = {valid: false, message: 'Quantity for ' + itemName + ' exceeds selected batch stock (' + available + ').'};
                }
            }
        });
        return result;
    }

    function sanitizeQuantityInput($input) {
        var allowsDecimal = String($input.attr('data-allows-decimal') || '0') === '1';
        var rawValue = String($input.val() || '');
        if (!rawValue) {
            return false;
        }
        if (allowsDecimal) {
            return false;
        }
        var sanitized = rawValue.replace(/[^\d-]/g, '');
        if (sanitized !== rawValue) {
            $input.val(sanitized);
            return true;
        }
        return false;
    }

    function recalcRow($row, sourceClass) {
        var qty = num($row.find('.sales-qty').val());
        var rate = num($row.find('.sales-rate').val());
        var taxable = num($row.find('.sales-taxable').val());
        var discountPercent = Math.max(0, Math.min(100, num($row.find('.sales-discount-percent').val())));
        var allowNonGst = $row.closest('.sales-gst-box').data('allowNonGst') === 1 || $row.closest('.sales-gst-box').attr('data-allow-non-gst') === '1';
        var bucket = allowNonGst ? (dropdownValue($row.find('.sales-bucket')) || 'gst') : 'gst';
        var nonGstMode = bucket === 'non_gst';
        taxable = (qty * rate) - ((qty * rate) * discountPercent / 100);
        var gstRate = nonGstMode ? 0 : num($row.find('.sales-gst-rate').val());
        var gstAmount = nonGstMode ? 0 : (taxable * gstRate / 100);
        if (nonGstMode) {
            $row.find('.sales-gst-rate').val('0.00');
        } else {
            $row.find('.sales-gst-rate').val(gstRate.toFixed(2));
        }
        $row.find('.sales-taxable').val(taxable.toFixed(2));
        $row.find('.sales-gst-amount').val(gstAmount.toFixed(2));
        var total = taxable + gstAmount;
        $row.find('.sales-total').val(total.toFixed(2));
        $row.find('.sales-inline-taxable').text(taxable.toFixed(2));
        $row.find('.sales-inline-gst').text(gstAmount.toFixed(2));
        $row.find('.sales-inline-total').text(total.toFixed(2));
    }

    function collect($box, hiddenSelector, syncTotals) {
        var taxableTotal = 0;
        var gstTotal = 0;
        var grandTotal = 0;
        var roundedPayable = 0;
        var roundOff = 0;
        var rows = [];
        $box.find('.sales-line-list .sales-gst-row').each(function () {
            var $row = $(this);
            var payload = rowPayload($row);
            if (!payload) {
                return;
            }
            rows.push(payload);
            taxableTotal += payload.taxableAmount;
            gstTotal += payload.gstAmount;
            grandTotal += payload.lineTotal;
        });
        roundedPayable = Math.round(grandTotal);
        roundOff = roundedPayable - grandTotal;
        $box.find('.sales-taxable-total').text(taxableTotal.toFixed(2));
        $box.find('.sales-gst-total').text(gstTotal.toFixed(2));
        $box.find('.sales-roundoff-sign').text(roundOff >= 0 ? '+' : '-');
        $box.find('.sales-roundoff-total').text(Math.abs(roundOff).toFixed(2));
        $box.find('.sales-grand-total').text(roundedPayable.toFixed(2));
        $box.find(hiddenSelector).val(JSON.stringify(rows));
        if (typeof syncTotals === 'function') {
            syncTotals({
                taxableTotal: taxableTotal,
                gstTotal: gstTotal,
                grandTotal: grandTotal,
                roundOff: roundOff,
                roundedPayable: roundedPayable
            });
        }
        return rows;
    }

    window.SalesInvoiceLines = {
        init: function (config) {
            var $box = $(config.containerSelector);
            if (!$box.length) {
                return null;
            }
            var allowNonGst = String($box.data('allowNonGst') || $box.attr('data-allow-non-gst') || '1') !== '0';
            $box.data('salesLineConfig', config);
            $.get(config.catalogUrl, function (response) {
                var optionsHtml = (response.data || []).map(function (item) { return itemOption(item, allowNonGst); }).join('');

                function addRow(row) {
                    $box.find('.sales-line-list').append(rowHtml(optionsHtml, allowNonGst, row));
                    var $row = $box.find('.sales-line-list .sales-gst-row').last();
                    $row.find('.sales-item').dropdown({
                        fullTextSearch: true,
                        onChange: function () {
                            if (!$row.data('loadingInitialRow')) {
                                resetBatchSelection($row);
                            }
                            applySelectedDefaults($row, {forceItemDefaults: !$row.data('loadingInitialRow')});
                            applyQuantityStep($row);
                            loadBatchOptions($row);
                            recalcRow($row, 'sales-item');
                            collect($box, config.hiddenInputSelector, config.syncTotals);
                        }
                    });
                    $row.find('.sales-bucket').dropdown({
                        onChange: function () {
                            if (!$row.data('loadingInitialRow')) {
                                resetBatchSelection($row);
                            }
                            updateHint($row);
                            loadBatchOptions($row);
                            collect($box, config.hiddenInputSelector, config.syncTotals);
                        }
                    });
                    $row.find('.sales-batch-select').dropdown({
                        fullTextSearch: true,
                        onChange: function (value) {
                            var $selected = $row.find('.sales-batch-select option[value="' + value + '"]');
                            $row.find('.sales-batch-id').val(value || '');
                            if (value) {
                                $row.find('.sales-batch-number').val($selected.data('batch-number') || '');
                                $row.find('.sales-expiry-date').val($selected.data('expiry-date') || '');
                            } else {
                                $row.find('.sales-batch-number').val('');
                                $row.find('.sales-expiry-date').val('');
                            }
                            updateBatchMeta($row);
                            collect($box, config.hiddenInputSelector, config.syncTotals);
                        }
                    });
                    if (row && row.itemId) {
                        $row.data('loadingInitialRow', true);
                        $row.find('.sales-item').dropdown('set selected', String(row.itemId));
                        $row.find('.sales-bucket').dropdown('set selected', row.stockBucket || 'gst');
                        $row.data('loadingInitialRow', false);
                    }
                    applySelectedDefaults($row);
                    applyQuantityStep($row);
                    updateBatchMeta($row);
                    loadBatchOptions($row);
                    recalcRow($row);
                    collect($box, config.hiddenInputSelector, config.syncTotals);
                }

                function setRows(rows) {
                    $box.find('.sales-line-list').html('');
                    if (rows && rows.length) {
                        rows.forEach(function (row) { addRow(row); });
                    } else {
                        addRow({});
                    }
                }

                $box.on('click', '.sales-add-row', function () {
                    addRow({});
                });
                $box.on('click', '.sales-remove', function () {
                    $(this).closest('.sales-gst-row').remove();
                    collect($box, config.hiddenInputSelector, config.syncTotals);
                });
                $box.on('click', '.sales-batch-trace', function () {
                    showBatchTrace($(this).closest('.sales-gst-row'));
                });
                $box.on('input change', '.sales-qty, .sales-rate, .sales-discount-percent, .sales-taxable, .sales-gst-rate, .sales-gst-amount, .sales-remark, .sales-batch-number, .sales-expiry-date', function () {
                    var $row = $(this).closest('.sales-gst-row');
                    if ($(this).hasClass('sales-qty')) {
                        sanitizeQuantityInput($(this));
                    }
                    recalcRow($row, $(this).attr('class') || '');
                    collect($box, config.hiddenInputSelector, config.syncTotals);
                });
                $box.on('keydown', '.sales-qty', function (event) {
                    var allowsDecimal = String($(this).attr('data-allows-decimal') || '0') === '1';
                    if (!allowsDecimal && (event.key === '.' || event.key === ',')) {
                        event.preventDefault();
                    }
                });
                setRows(config.initialRows || []);
                $box.data('salesInvoiceLinesApi', {
                    collect: function () { return collect($box, config.hiddenInputSelector, config.syncTotals); },
                    setRows: setRows,
                    validate: function () { return validateRows($box); }
                });
            });
            return {
                collect: function () {
                    var api = $box.data('salesInvoiceLinesApi');
                    return api ? api.collect() : [];
                },
                setRows: function (rows) {
                    var api = $box.data('salesInvoiceLinesApi');
                    if (api) api.setRows(rows || []);
                },
                validate: function () {
                    var api = $box.data('salesInvoiceLinesApi');
                    return api && typeof api.validate === 'function' ? api.validate() : {valid: true, message: ''};
                }
            };
        }
    };
})(window, window.jQuery);
