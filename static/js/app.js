/* ── CRM App JavaScript ── */

document.addEventListener('DOMContentLoaded', function () {
    // -- Sidebar toggle (mobile) --
    const sidebarToggle = document.getElementById('sidebarToggle');
    const sidebar = document.getElementById('sidebar');

    if (sidebarToggle && sidebar) {
        sidebarToggle.addEventListener('click', function () {
            sidebar.classList.toggle('show');
        });

        // Close sidebar when clicking outside on mobile
        document.addEventListener('click', function (e) {
            if (window.innerWidth < 768 &&
                sidebar.classList.contains('show') &&
                !sidebar.contains(e.target) &&
                e.target !== sidebarToggle &&
                !sidebarToggle.contains(e.target)) {
                sidebar.classList.remove('show');
            }
        });
    }

    function createCalculatorField(field) {
        const wrapper = document.createElement('div');
        wrapper.className = 'col-12 col-md-6';

        const fieldName = String(field.name || '').trim();
        const fieldType = String(field.type || 'number').trim().toLowerCase();
        const labelText = String(field.label || fieldName || 'Field');

        const label = document.createElement('label');
        label.className = 'form-label small mb-1';
        label.textContent = labelText;

        let input;

        if (fieldType === 'boolean' || fieldType === 'bool' || fieldType === 'checkbox') {
            const checkWrap = document.createElement('div');
            checkWrap.className = 'form-check mt-1';

            input = document.createElement('input');
            input.type = 'checkbox';
            input.className = 'form-check-input';
            input.id = `calc-${fieldName}`;
            input.dataset.fieldName = fieldName;
            input.checked = Boolean(field.default);

            const checkLabel = document.createElement('label');
            checkLabel.className = 'form-check-label';
            checkLabel.setAttribute('for', input.id);
            checkLabel.textContent = field.hint || 'Да';

            checkWrap.appendChild(input);
            checkWrap.appendChild(checkLabel);
            wrapper.appendChild(label);
            wrapper.appendChild(checkWrap);
            return wrapper;
        }

        if (fieldType === 'select' || fieldType === 'enum' || fieldType === 'choice') {
            input = document.createElement('select');
            input.className = 'form-select';
            input.dataset.fieldName = fieldName;

            const emptyOpt = document.createElement('option');
            emptyOpt.value = '';
            emptyOpt.textContent = 'Выберите...';
            input.appendChild(emptyOpt);

            let options = Array.isArray(field.options) ? field.options : [];

            if (!options.length && field.choices && typeof field.choices === 'object' && !Array.isArray(field.choices)) {
                options = Object.entries(field.choices).map(function (entry) {
                    return { value: String(entry[0]), label: String(entry[0]), price: Number(entry[1] || 0) };
                });
            }

            options.forEach(function (opt) {
                if (!opt || typeof opt !== 'object') return;
                const option = document.createElement('option');
                option.value = String(opt.value ?? opt.name ?? '');
                option.textContent = String(opt.label ?? opt.name ?? option.value);
                input.appendChild(option);
            });

            if (field.default !== undefined && field.default !== null) {
                input.value = String(field.default);
            }
        } else {
            input = document.createElement('input');
            input.type = 'number';
            input.className = 'form-control';
            input.step = 'any';
            input.dataset.fieldName = fieldName;

            if (field.default !== undefined && field.default !== null && field.default !== '') {
                input.value = field.default;
            }
        }

        wrapper.appendChild(label);
        wrapper.appendChild(input);
        return wrapper;
    }

    function initServiceCalculator() {
        const form = document.getElementById('service-calculator-form');
        const serviceSearchInput = document.getElementById('calculator-service-search');
        const serviceSuggest = document.getElementById('service-suggestions');
        const serviceSelect = document.getElementById('calculator-service');
        const clientPhoneInput = document.getElementById('calculator-client-phone');
        const clientNameInput = document.getElementById('calculator-client-name');
        const clientPhoneSuggest = document.getElementById('client-phone-suggestions');
        const executorSelect = document.getElementById('calculator-executor');
        const executorLoad = document.getElementById('calculator-executor-load');
        const autoAssignBtn = document.getElementById('calculator-autoassign');
        const basePriceEl = document.getElementById('calculator-base-price');
        const totalEl = document.getElementById('calculator-total');
        const fieldsContainer = document.getElementById('calculator-fields');
        const statusEl = document.getElementById('calculator-status');
        const createOrderBtn = document.getElementById('calculator-create-order');

        if (!form || !serviceSearchInput || !serviceSelect || !clientPhoneInput || !clientNameInput || !executorSelect || !fieldsContainer || !statusEl || !createOrderBtn) {
            return;
        }

        let services = [];
        let selectedService = null;
        let selectedSchema = {};
        let executors = [];
        let lastCalculation = null;
        let calcTimer = null;
        let clientTimer = null;

        function setStatus(text, isError) {
            statusEl.textContent = text || '';
            statusEl.className = isError ? 'small text-danger' : 'small text-muted';
        }

        function formatMoney(value) {
            const numeric = Number(value);
            if (Number.isNaN(numeric)) return String(value ?? '0');
            return new Intl.NumberFormat('ru-RU').format(numeric);
        }

        function setTotals(basePrice, total, currency) {
            const cur = String(currency || 'RUB');
            basePriceEl.textContent = `${formatMoney(basePrice || 0)} ${cur}`;
            totalEl.textContent = `${formatMoney(total || 0)} ${cur}`;
        }

        function _readFieldOptions(field) {
            let options = Array.isArray(field.options) ? field.options : [];
            if (!options.length && field.choices && typeof field.choices === 'object' && !Array.isArray(field.choices)) {
                options = Object.entries(field.choices).map(function (entry) {
                    return { value: String(entry[0]), label: String(entry[0]), price: Number(entry[1] || 0) };
                });
            }
            return options;
        }

        function renderSchemaFields(schema) {
            const fields = Array.isArray((schema || {}).fields) ? schema.fields : [];
            if (!fields.length) {
                fieldsContainer.innerHTML = '<span class="small text-muted">Без параметров</span>';
                return;
            }

            const nodes = fields.map(function (field) {
                if (!field || typeof field !== 'object' || !field.name) return '';

                const name = String(field.name || '').trim();
                const label = String(field.label || name);
                const type = String(field.type || 'number').toLowerCase();

                if (type === 'select' || type === 'enum' || type === 'choice') {
                    const options = _readFieldOptions(field).map(function (opt) {
                        const v = String(opt.value ?? opt.name ?? '');
                        const l = String(opt.label ?? opt.name ?? v);
                        return `<option value="${v}">${l}</option>`;
                    }).join('');
                    return `
                        <div class="calc-field-item">
                            <label class="form-label small mb-1">${label}</label>
                            <select class="form-select form-select-sm" data-field-name="${name}">
                                <option value="">-</option>
                                ${options}
                            </select>
                        </div>
                    `;
                }

                if (type === 'boolean' || type === 'bool' || type === 'checkbox') {
                    return `
                        <div class="calc-field-item calc-field-check">
                            <label class="form-label small mb-1">${label}</label>
                            <div class="form-check mt-1">
                                <input class="form-check-input" type="checkbox" data-field-name="${name}">
                            </div>
                        </div>
                    `;
                }

                const initial = field.default !== undefined && field.default !== null ? field.default : '';
                return `
                    <div class="calc-field-item">
                        <label class="form-label small mb-1">${label}</label>
                        <input class="form-control form-control-sm" type="number" step="any" value="${initial}" data-field-name="${name}">
                    </div>
                `;
            }).filter(Boolean);

            fieldsContainer.innerHTML = `<div class="calc-fields-grid">${nodes.join('')}</div>`;
        }

        function collectPayload() {
            const payload = {};
            fieldsContainer.querySelectorAll('[data-field-name]').forEach(function (input) {
                const name = input.dataset.fieldName;
                if (!name) return;
                if (input.type === 'checkbox') {
                    payload[name] = Boolean(input.checked);
                    return;
                }
                if (input.tagName === 'SELECT') {
                    payload[name] = input.value;
                    return;
                }
                if (input.type === 'number') {
                    payload[name] = input.value === '' ? 0 : Number(input.value);
                    return;
                }
                payload[name] = input.value;
            });
            return payload;
        }

        function setSelectedServiceById(serviceId) {
            selectedService = services.find(function (item) { return Number(item.id) === Number(serviceId); }) || null;
            selectedSchema = (selectedService && selectedService.calculator_schema) || {};
            renderSchemaFields(selectedSchema);
            setTotals(selectedService ? Number(selectedService.base_price || 0) : 0, 0, selectedSchema.currency || 'RUB');
            createOrderBtn.disabled = !selectedService;
            lastCalculation = null;
            if (selectedService) {
                recalculateLive();
            }
        }

        function renderServices(list) {
            services = Array.isArray(list) ? list : [];
            serviceSelect.innerHTML = '<option value="">Выберите услугу...</option>';
            serviceSuggest.innerHTML = '';

            services.forEach(function (service) {
                const option = document.createElement('option');
                option.value = String(service.id);
                option.textContent = `${service.name} (${formatMoney(service.base_price || 0)} RUB)`;
                serviceSelect.appendChild(option);

                const dl = document.createElement('option');
                dl.value = String(service.name || '');
                serviceSuggest.appendChild(dl);
            });
        }

        function pickBestExecutor() {
            const available = executors.filter(function (e) { return Boolean(e.is_active); });
            if (!available.length) return null;
            available.sort(function (a, b) {
                const loadA = Number(a.active_tasks || 0) / Math.max(Number(a.max_active_tasks || 1), 1);
                const loadB = Number(b.active_tasks || 0) / Math.max(Number(b.max_active_tasks || 1), 1);
                return loadA - loadB;
            });
            return available[0];
        }

        function updateExecutorMeta() {
            const selectedId = Number(executorSelect.value || 0);
            const ex = executors.find(function (item) { return Number(item.id) === selectedId; });
            if (!ex) {
                executorLoad.textContent = 'Загрузка: авто';
                return;
            }
            executorLoad.textContent = `Загрузка: ${ex.active_tasks}/${ex.max_active_tasks}`;
        }

        async function loadExecutors() {
            try {
                const response = await fetch('/api/v1/executors');
                if (!response.ok) throw new Error('executors_load_failed');
                executors = await response.json();

                executorSelect.innerHTML = '<option value="">Автоназначение</option>';
                executors.forEach(function (ex) {
                    if (!ex || !ex.is_active) return;
                    const option = document.createElement('option');
                    option.value = String(ex.id);
                    option.textContent = `${ex.name} (${ex.active_tasks}/${ex.max_active_tasks})`;
                    executorSelect.appendChild(option);
                });

                updateExecutorMeta();
            } catch (_err) {
                setStatus('Не удалось загрузить исполнителей.', true);
            }
        }

        async function loadServices(query) {
            const q = String(query || '').trim();
            try {
                const response = await fetch(`/api/v1/catalog/services?q=${encodeURIComponent(q)}`);
                if (!response.ok) throw new Error('services_load_failed');
                const data = await response.json();
                renderServices(data || []);
                if (services.length === 1 && q) {
                    serviceSelect.value = String(services[0].id);
                    setSelectedServiceById(services[0].id);
                }
            } catch (_err) {
                setStatus('Не удалось загрузить услуги.', true);
            }
        }

        async function searchClients(phone) {
            const q = String(phone || '').trim();
            if (q.length < 2) {
                clientPhoneSuggest.innerHTML = '';
                return;
            }
            try {
                const response = await fetch(`/api/v1/orders/clients/search?phone=${encodeURIComponent(q)}`);
                if (!response.ok) throw new Error('client_search_failed');
                const clients = await response.json();
                clientPhoneSuggest.innerHTML = '';
                (clients || []).forEach(function (client) {
                    const option = document.createElement('option');
                    option.value = String(client.phone || '');
                    option.label = `${client.name || ''}`;
                    option.dataset.clientName = String(client.name || '');
                    clientPhoneSuggest.appendChild(option);
                });

                const exact = (clients || []).find(function (c) { return String(c.phone || '') === q; });
                if (exact && !String(clientNameInput.value || '').trim()) {
                    clientNameInput.value = String(exact.name || '');
                }
            } catch (_err) {
                setStatus('Ошибка поиска клиента.', true);
            }
        }

        async function recalculateLive() {
            if (!selectedService) {
                setTotals(0, 0, 'RUB');
                lastCalculation = null;
                createOrderBtn.disabled = true;
                return;
            }

            const payload = collectPayload();
            try {
                const response = await fetch(`/api/v1/catalog/services/${selectedService.id}/calculate`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload),
                });

                if (!response.ok) throw new Error('calculate_failed');
                const data = await response.json();
                if (!data.ok) throw new Error(data.error || 'calculate_failed');

                const currency = data.currency || selectedSchema.currency || 'RUB';
                setTotals(selectedService.base_price || 0, data.total || 0, currency);
                lastCalculation = {
                    serviceId: selectedService.id,
                    payload,
                    total: data.total,
                    currency,
                };
                createOrderBtn.disabled = false;
                setStatus('', false);
            } catch (_err) {
                setStatus('Ошибка пересчета.', true);
                createOrderBtn.disabled = true;
            }
        }

        function scheduleRecalculate() {
            window.clearTimeout(calcTimer);
            calcTimer = window.setTimeout(function () {
                recalculateLive();
            }, 180);
        }

        form.addEventListener('submit', function (event) {
            event.preventDefault();
        });

        fieldsContainer.addEventListener('input', scheduleRecalculate);
        fieldsContainer.addEventListener('change', scheduleRecalculate);

        serviceSelect.addEventListener('change', function () {
            setSelectedServiceById(Number(serviceSelect.value || 0));
        });

        serviceSearchInput.addEventListener('input', function () {
            const q = String(serviceSearchInput.value || '').trim();
            loadServices(q);
        });

        serviceSearchInput.addEventListener('change', function () {
            const name = String(serviceSearchInput.value || '').trim().toLowerCase();
            const match = services.find(function (service) { return String(service.name || '').trim().toLowerCase() === name; });
            if (match) {
                serviceSelect.value = String(match.id);
                setSelectedServiceById(match.id);
            }
        });

        clientPhoneInput.addEventListener('input', function () {
            window.clearTimeout(clientTimer);
            clientTimer = window.setTimeout(function () {
                searchClients(clientPhoneInput.value || '');
            }, 220);
        });

        executorSelect.addEventListener('change', function () {
            updateExecutorMeta();
        });

        autoAssignBtn.addEventListener('click', function () {
            const best = pickBestExecutor();
            if (!best) {
                setStatus('Нет доступных исполнителей.', true);
                return;
            }
            executorSelect.value = String(best.id);
            updateExecutorMeta();
            setStatus(`Автоназначение: ${best.name}`, false);
        });

        createOrderBtn.addEventListener('click', async function () {
            const clientName = String(clientNameInput.value || '').trim();
            const clientPhone = String(clientPhoneInput.value || '').trim();
            if (!clientPhone) {
                setStatus('Укажите телефон клиента.', true);
                return;
            }
            if (!clientName) {
                setStatus('Укажите имя клиента.', true);
                return;
            }
            if (!lastCalculation) {
                setStatus('Выберите услугу и параметры.', true);
                return;
            }

            createOrderBtn.disabled = true;
            setStatus('Создаем заказ...', false);

            try {
                const preferredExecutorId = Number(executorSelect.value || 0);
                const payload = {
                    source_channel: 'calculator_pos',
                    currency: lastCalculation.currency,
                    preferred_executor_id: preferredExecutorId || undefined,
                    client: {
                        name: clientName,
                        phone: clientPhone,
                    },
                    items: [
                        {
                            service_id: lastCalculation.serviceId,
                            quantity: 1,
                            calculator_payload: lastCalculation.payload,
                        },
                    ],
                };

                const response = await fetch('/api/v1/orders', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload),
                });

                if (!response.ok) throw new Error('order_create_failed');
                const data = await response.json();
                setStatus(`Заказ создан: ${data.order_no || data.id}`, false);
            } catch (_err) {
                setStatus('Не удалось создать заказ.', true);
            } finally {
                createOrderBtn.disabled = false;
            }
        });

        loadServices('');
        loadExecutors();
        renderSchemaFields({ fields: [] });
        setTotals(0, 0, 'RUB');
    }

    initServiceCalculator();

    // -- HTMX events --
    function applyRowStatusColor(selectEl, rowBgColor) {
        const row = selectEl.closest('tr.record-row');
        if (!row) return;

        if (rowBgColor) {
            row.classList.remove('status-row--default');
            row.classList.add('status-row--custom');
            row.style.setProperty('--status-row-color', rowBgColor);
        } else {
            row.classList.remove('status-row--custom');
            row.classList.add('status-row--default');
            row.style.removeProperty('--status-row-color');
        }
    }

    function getSelectedOptionColor(selectEl) {
        const option = selectEl.selectedOptions && selectEl.selectedOptions[0];
        if (!option) return '';
        return option.dataset.color || '';
    }

    function toRgba(hexColor, alpha) {
        const value = (hexColor || '').trim().replace('#', '');
        if (!/^[0-9a-fA-F]{6}$/.test(value)) return '';
        const r = parseInt(value.slice(0, 2), 16);
        const g = parseInt(value.slice(2, 4), 16);
        const b = parseInt(value.slice(4, 6), 16);
        return `rgba(${r}, ${g}, ${b}, ${alpha})`;
    }

    function refreshRecordsContainer(slug) {
        const container = document.getElementById('records-container');
        if (!container || !slug) return;
        const searchInput = document.querySelector("input[name='search']");
        const sortSelect = document.querySelector("select[name='sort']");
        const search = encodeURIComponent(searchInput ? searchInput.value : '');
        const sort = encodeURIComponent(sortSelect ? sortSelect.value : 'newest');
        htmx.ajax('GET', `/modules/${slug}?search=${search}&sort=${sort}`, {
            target: '#records-container',
            swap: 'innerHTML'
        });
    }

    async function saveInlineStatus(selectEl) {
        const slug = selectEl.dataset.slug;
        const recordId = selectEl.dataset.recordId;
        const field = selectEl.dataset.field;
        const value = selectEl.value;
        const previousValue = selectEl.dataset.previousValue || '';
        const rowBg = toRgba(getSelectedOptionColor(selectEl), 0.28);

        applyRowStatusColor(selectEl, rowBg);

        const formData = new FormData();
        formData.append('field', field);
        formData.append('value', value);

        try {
            const response = await fetch(`/modules/${slug}/record/${recordId}/field`, {
                method: 'POST',
                body: formData,
                headers: { 'HX-Request': 'true' }
            });

            if (!response.ok) {
                throw new Error('Save failed');
            }

            const payload = await response.json();
            selectEl.dataset.previousValue = value;
            applyRowStatusColor(selectEl, payload.row_bg || rowBg);
            refreshRecordsContainer(slug);
        } catch (err) {
            selectEl.value = previousValue;
            applyRowStatusColor(selectEl, toRgba(getSelectedOptionColor(selectEl), 0.28));
            alert('Не удалось сохранить статус. Обновите страницу и попробуйте снова.');
        }
    }

    async function savePartnerIssued(checkboxEl) {
        const slug = checkboxEl.dataset.slug;
        const recordId = checkboxEl.dataset.recordId;
        const field = checkboxEl.dataset.field;
        const checked = checkboxEl.checked;
        checkboxEl.disabled = true;

        const formData = new FormData();
        formData.append('field', field);
        formData.append('value', checked ? 'true' : 'false');

        try {
            const response = await fetch(`/modules/${slug}/record/${recordId}/field`, {
                method: 'POST',
                body: formData,
                headers: { 'HX-Request': 'true' }
            });
            if (!response.ok) {
                throw new Error('Save failed');
            }
            refreshRecordsContainer(slug);
        } catch (err) {
            checkboxEl.checked = !checked;
            alert('Не удалось обновить отметку выдачи.');
        } finally {
            checkboxEl.disabled = false;
        }
    }

    function initStatusSelects(root) {
        root.querySelectorAll('.js-status-select').forEach(function (selectEl) {
            if (!selectEl.dataset.previousValue) {
                selectEl.dataset.previousValue = selectEl.value || '';
            }
            applyRowStatusColor(selectEl, toRgba(getSelectedOptionColor(selectEl), 0.28));
        });
    }

    function getOrdersBulkPanel() {
        return document.getElementById('orders-bulk-status-panel');
    }

    function getOrderRowChecks() {
        const container = document.getElementById('records-container') || document;
        return Array.from(container.querySelectorAll('.js-order-bulk-check'));
    }

    function updateOrdersBulkState() {
        const panel = getOrdersBulkPanel();
        if (!panel) return;

        const applyBtn = panel.querySelector('.js-orders-bulk-status-apply');
        const selectedCountEl = panel.querySelector('.js-orders-selected-count');
        const checks = getOrderRowChecks();
        const selected = checks.filter(function (input) { return input.checked; }).length;

        if (selectedCountEl) selectedCountEl.textContent = String(selected);
        if (applyBtn) applyBtn.disabled = selected === 0;

        const selectAll = document.querySelector('.js-order-select-all');
        if (selectAll) {
            selectAll.checked = checks.length > 0 && selected === checks.length;
            selectAll.indeterminate = selected > 0 && selected < checks.length;
        }
    }

    function initOrdersBulkStatus(_root) {
        if (!document.body.dataset.ordersBulkBound) {
            document.body.addEventListener('change', function (event) {
                if (event.target.closest('.js-order-bulk-check') || event.target.closest('.js-order-select-all')) {
                    const selectAll = event.target.closest('.js-order-select-all');
                    if (selectAll) {
                        getOrderRowChecks().forEach(function (input) {
                            input.checked = selectAll.checked;
                        });
                    }
                    updateOrdersBulkState();
                }
            });

            document.body.addEventListener('click', async function (event) {
                const applyBtn = event.target.closest('.js-orders-bulk-status-apply');
                if (!applyBtn) return;

                const panel = getOrdersBulkPanel();
                if (!panel) return;
                const slug = panel.dataset.slug;
                const field = panel.dataset.field;
                const statusSelect = panel.querySelector('.js-orders-bulk-status-value');

                const selectedIds = getOrderRowChecks()
                    .filter(function (input) { return input.checked; })
                    .map(function (input) { return input.dataset.recordId; })
                    .filter(Boolean);

                if (!selectedIds.length) {
                    updateOrdersBulkState();
                    return;
                }

                applyBtn.disabled = true;
                try {
                    const formData = new FormData();
                    formData.append('field', field || '');
                    formData.append('value', statusSelect ? statusSelect.value : '');
                    selectedIds.forEach(function (id) { formData.append('record_ids', id); });

                    const response = await fetch(`/modules/${slug}/bulk/status`, {
                        method: 'POST',
                        body: formData,
                        headers: { 'HX-Request': 'true' }
                    });
                    if (!response.ok) {
                        throw new Error('bulk_status_failed');
                    }
                    refreshRecordsContainer(slug);
                } catch (_err) {
                    alert('Не удалось массово обновить статус.');
                    updateOrdersBulkState();
                }
            });

            document.body.dataset.ordersBulkBound = '1';
        }

        updateOrdersBulkState();
    }

    initStatusSelects(document);
    initOrdersBulkStatus(document);

    // Re-init Bootstrap modal after HTMX swap
    document.body.addEventListener('htmx:afterSwap', function (event) {
        // If modal content was swapped, show modal
        if (event.detail.target.id === 'modal-container') {
            const modalEl = document.getElementById('recordModal');
            if (modalEl) {
                const modal = bootstrap.Modal.getOrCreateInstance(modalEl);
                modal.show();
            }
        }

        if (event.detail.target.id === 'records-container') {
            initStatusSelects(event.detail.target);
            initOrdersBulkStatus(event.detail.target);
        }
    });

    document.body.addEventListener('click', function (event) {
        if (event.target.closest('.js-status-select') || event.target.closest('.js-partner-issued-checkbox')) {
            event.stopPropagation();
        }
    }, true);

    document.body.addEventListener('change', function (event) {
        const selectEl = event.target.closest('.js-status-select');
        if (selectEl) {
            event.stopPropagation();
            saveInlineStatus(selectEl);
            return;
        }

        const checkboxEl = event.target.closest('.js-partner-issued-checkbox');
        if (checkboxEl) {
            event.stopPropagation();
            savePartnerIssued(checkboxEl);
        }
    });

    // Handle HX-Redirect header
    document.body.addEventListener('htmx:beforeSwap', function (event) {
        const xhr = event.detail.xhr;
        if (xhr && xhr.getResponseHeader('HX-Redirect')) {
            window.location.href = xhr.getResponseHeader('HX-Redirect');
            event.detail.shouldSwap = false;
        }
    });

    // Close modal after successful save
    document.body.addEventListener('htmx:afterRequest', function (event) {
        if (event.detail.successful && event.detail.target &&
            event.detail.target.id === 'save-result') {
            // Auto-close modal after 1.5s on success
            setTimeout(function () {
                const modalEl = document.getElementById('recordModal');
                if (modalEl) {
                    const modal = bootstrap.Modal.getInstance(modalEl);
                    if (modal) modal.hide();
                    // Refresh table
                    const container = document.getElementById('records-container');
                    if (container) {
                        htmx.trigger(container, 'refresh');
                    }
                }
            }, 1500);
        }
    });
});
