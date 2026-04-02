(function (window, $) {
    function asNumber(value) {
        var parsed = parseFloat(value);
        return isNaN(parsed) ? 0 : parsed;
    }

    function optionHtml(item, allowNonGst) {
        var skuLabel = item.SKU ? ' [' + item.SKU + ']' : '';
        return '<option value="' + item.ID + '" data-default-bucket="' + item.DefaultStockBucket + '" data-price="' + (item.DefaultSalesPrice || 0) + '" data-gst-rate="' + (item.GSTRate || 0) + '" data-gst-stock="' + (item.GSTStock || 0) + '" data-non-gst-stock="' + (item.NonGSTStock || 0) + '" data-unit-name="' + (item.UnitName || "") + '" data-unit-allows-decimal="' + (item.UnitAllowsDecimal ? 1 : 0) + '">' +
            item.Name + skuLabel + '</option>';
    }

    function getHiddenInput($container, selector) {
        var $input = $container.find(selector);
        return $input.length ? $input : $(selector);
    }

    function buildRow(itemOptionsHtml, row, config) {
        row = row || {};
        var bucketOptions = '<option value="gst">GST</option>';
        if (config.allowNonGst !== false) {
            bucketOptions += '<option value="non_gst">Non GST</option>';
        }
        var hintHtml = config.allowNonGst !== false ? 'GST Stock: 0 | Non-GST Stock: 0' : 'GST Stock: 0';
        return '' +
            '<div class="inventory-row inventory-line-card">' +
            '<div class="inventory-line-card-header">' +
            '<button type="button" class="ui circular youtube icon button inventory-remove" data-tooltip="Remove Row" data-position="left center" data-variation="mini" style="font-size:10px;"><i class="trash alternate icon"></i></button>' +
            '</div>' +
            '<div class="inventory-grid">' +
            '<div class="inventory-span-5"><div class="field"><label>Item</label><select class="ui fluid search selection dropdown inventory-item"><option value="">Select item</option>' + itemOptionsHtml + '</select><div class="inventory-inline-meta"><span class="inventory-unit-badge">Unit: <span class="inventory-unit-name">-</span></span><span class="inventory-stock-hint">' + hintHtml + '</span></div></div></div>' +
            '<div class="inventory-span-2"><div class="field"><label>Bucket</label><select class="ui fluid selection dropdown inventory-bucket">' + bucketOptions + '</select></div></div>' +
            '<div class="inventory-span-2"><div class="field"><label>Qty</label><div class="ui fluid input"><input type="number" class="inventory-qty" min="0" step="0.01" value="' + (row.quantity || '') + '"></div></div></div>' +
            '<div class="inventory-span-3"><div class="field"><label>Rate</label><div class="ui fluid input"><input type="number" class="inventory-rate" min="0" step="0.01" value="' + (row.unitPrice || row.unitCost || '') + '"></div></div></div>' +
            '<div class="inventory-span-2"><div class="field"><label>Total</label><div class="ui fluid input"><input type="number" class="inventory-total" min="0" step="0.01" value="' + (row.lineTotal || '') + '"></div></div></div>' +
            '<div class="inventory-span-4"><div class="field"><label>Remark</label><div class="ui fluid input"><input type="text" class="inventory-remark" maxlength="120" value="' + (row.remark || '') + '" placeholder="Optional"></div></div></div>' +
            '</div>' +
            '<div class="inventory-line-summary"><span class="inventory-summary-pill">Total ₹ <span class="inventory-inline-total">' + (asNumber(row.lineTotal || 0).toFixed(2)) + '</span></span></div>' +
            '</div>';
    }

    function updateSummary($container, config) {
        var total = 0;
        var rows = [];
        $container.find('.inventory-line-list .inventory-row').each(function () {
            var $row = $(this);
            var itemId = dropdownValue($row.find('.inventory-item'));
            var quantity = asNumber($row.find('.inventory-qty').val());
            var unitRate = asNumber($row.find('.inventory-rate').val());
            var lineTotal = asNumber($row.find('.inventory-total').val()) || (quantity * unitRate);
            if (!itemId || quantity <= 0) {
                return;
            }
            rows.push({
                itemId: itemId,
                stockBucket: $row.find('.inventory-bucket').val() || 'gst',
                quantity: quantity,
                unitPrice: unitRate,
                unitCost: unitRate,
                taxableAmount: lineTotal,
                gstRate: 0,
                gstAmount: 0,
                lineTotal: lineTotal,
                remark: $row.find('.inventory-remark').val() || ''
            });
            total += lineTotal;
            $row.find('.inventory-inline-total').text(lineTotal.toFixed(2));
        });
        $container.find('.inventory-total-display').text(total.toFixed(2));
        $container.find('.inventory-payable-display').text(total.toFixed(2));
        getHiddenInput($container, config.hiddenInputSelector).val(JSON.stringify(rows));
        if (config.amountInputSelector && rows.length && typeof config.syncAmount === 'function') {
            config.syncAmount(total);
        }
    }

    function applyRowDefaults($row) {
        var $selected = selectedItemOption($row);
        if (!$selected.length) {
            return;
        }
        if (!dropdownValue($row.find('.inventory-bucket'))) {
            try {
                $row.find('.inventory-bucket').dropdown('set selected', $selected.data('default-bucket') || 'gst');
            } catch (e) {
                $row.find('.inventory-bucket').val($selected.data('default-bucket') || 'gst');
            }
        }
        if (!$row.find('.inventory-rate').val()) {
            $row.find('.inventory-rate').val($selected.data('price') || 0);
        }
        applyQuantityStep($row);
        updateStockHint($row);
    }

    function updateStockHint($row) {
        var $selected = selectedItemOption($row);
        var gstStock = Number($selected.data('gst-stock') || 0);
        var nonGstStock = Number($selected.data('non-gst-stock') || 0);
        var unitName = $selected.data('unit-name') || '-';
        var allowNonGst = $row.closest('.inventory-line-item-box').data('allowNonGst') === 1 || $row.closest('.inventory-line-item-box').attr('data-allow-non-gst') === '1';
        var summary = 'GST Stock: ' + gstStock;
        if (allowNonGst) {
            summary += ' | Non-GST Stock: ' + nonGstStock;
        }
        $row.find('.inventory-unit-name').text(unitName);
        $row.find('.inventory-stock-hint').text(summary);
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
        var itemId = dropdownValue($row.find('.inventory-item'));
        if (!itemId) {
            return $();
        }
        return $row.find('.inventory-item option[value="' + itemId + '"]');
    }

    function applyQuantityStep($row) {
        var $selected = selectedItemOption($row);
        var allowsDecimal = String($selected.data('unit-allows-decimal') || '0') === '1';
        var $qty = $row.find('.inventory-qty');
        $qty.attr('step', allowsDecimal ? '0.01' : '1');
        $qty.attr('placeholder', allowsDecimal ? '0.00' : '0');
        $qty.attr('data-allows-decimal', allowsDecimal ? '1' : '0');
        if (!allowsDecimal) {
            var currentValue = String($qty.val() || '').trim();
            if (currentValue && currentValue.indexOf('.') >= 0) {
                $qty.val(String(Math.trunc(asNumber(currentValue))));
            }
        }
    }

    function sanitizeQuantityInput($input) {
        var allowsDecimal = String($input.attr('data-allows-decimal') || '0') === '1';
        var rawValue = String($input.val() || '');
        if (!rawValue || allowsDecimal) {
            return;
        }
        $input.val(rawValue.replace(/[^\d-]/g, ''));
    }

    window.InventoryLineItems = {
        init: function (config) {
            var $container = $(config.containerSelector);
            if (!$container.length) {
                return null;
            }
            config.allowNonGst = config.allowNonGst !== false && String($container.data('allowNonGst') || $container.attr('data-allow-non-gst') || '1') !== '0';
            $container.off('.inventoryLineItems');
            $.get(config.catalogUrl, function (response) {
                var items = response.data || [];
                var optionsHtml = items.map(function (item) { return optionHtml(item, config.allowNonGst); }).join('');

                function addRow(rowData) {
                    $container.find('.inventory-line-list').append(buildRow(optionsHtml, rowData, config));
                    var $row = $container.find('.inventory-line-list .inventory-row').last();
                    $row.find('.inventory-item').dropdown({
                        fullTextSearch: true,
                        onChange: function () {
                            applyRowDefaults($row);
                            updateSummary($container, config);
                        }
                    });
                    $row.find('.inventory-bucket').dropdown({
                        onChange: function () {
                            updateStockHint($row);
                            updateSummary($container, config);
                        }
                    });
                    if (rowData && rowData.itemId) {
                        $row.find('.inventory-item').dropdown('set selected', String(rowData.itemId));
                        $row.find('.inventory-bucket').dropdown('set selected', rowData.stockBucket || 'gst');
                    }
                    applyRowDefaults($row);
                    updateSummary($container, config);
                }

                function setRows(rows) {
                    $container.find('.inventory-line-list').html('');
                    if (rows && rows.length) {
                        rows.forEach(function (row) { addRow(row); });
                    } else {
                        addRow({});
                    }
                    updateSummary($container, config);
                }

                $container.on('click.inventoryLineItems', '.inventory-add-row', function () {
                    addRow({});
                });

                $container.on('click.inventoryLineItems', '.inventory-remove', function () {
                    $(this).closest('.inventory-row').remove();
                    updateSummary($container, config);
                });

                $container.on('input.inventoryLineItems change.inventoryLineItems', '.inventory-qty, .inventory-rate, .inventory-total, .inventory-remark', function () {
                    var $row = $(this).closest('.inventory-row');
                    if ($(this).hasClass('inventory-qty')) {
                        sanitizeQuantityInput($(this));
                    }
                    applyRowDefaults($row);
                    var qty = asNumber($row.find('.inventory-qty').val());
                    var rate = asNumber($row.find('.inventory-rate').val());
                    if ($(this).hasClass('inventory-qty') || $(this).hasClass('inventory-rate')) {
                        $row.find('.inventory-total').val((qty * rate).toFixed(2));
                    }
                    updateSummary($container, config);
                });

                $container.on('keydown.inventoryLineItems', '.inventory-qty', function (event) {
                    var allowsDecimal = String($(this).attr('data-allows-decimal') || '0') === '1';
                    if (!allowsDecimal && (event.key === '.' || event.key === ',')) {
                        event.preventDefault();
                    }
                });

                setRows(config.initialRows || []);

                $container.data('inventoryLineItemsApi', {
                    collect: function () {
                        updateSummary($container, config);
                        return getHiddenInput($container, config.hiddenInputSelector).val() || '[]';
                    },
                    setRows: setRows
                });
            });
            return {
                collect: function () {
                    var api = $container.data('inventoryLineItemsApi');
                    if (api && typeof api.collect === 'function') {
                        return api.collect();
                    }
                    return getHiddenInput($container, config.hiddenInputSelector).val() || '[]';
                },
                setRows: function (rows) {
                    var api = $container.data('inventoryLineItemsApi');
                    if (api && typeof api.setRows === 'function') {
                        api.setRows(rows || []);
                    }
                }
            };
        }
    };
})(window, window.jQuery);
