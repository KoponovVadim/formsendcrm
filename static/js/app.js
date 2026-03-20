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

            (field.options || []).forEach(function (opt) {
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
        const serviceSelect = document.getElementById('calculator-service');
        const fieldsContainer = document.getElementById('calculator-fields');
        const statusEl = document.getElementById('calculator-status');
        const resultEl = document.getElementById('calculator-result');
        if (!form || !serviceSelect || !fieldsContainer || !statusEl || !resultEl) {
            return;
        }

        let services = [];
        let selectedSchema = {};

        function setStatus(text, isError) {
            statusEl.textContent = text || '';
            statusEl.className = isError ? 'small text-danger' : 'small text-muted';
        }

        function formatMoney(value) {
            const numeric = Number(value);
            if (Number.isNaN(numeric)) return String(value ?? '');
            return new Intl.NumberFormat('ru-RU').format(numeric);
        }

        function describeBreakdownItem(item, currency) {
            const t = String(item.type || '');

            if (t === 'base_price') {
                return `Базовая цена: ${formatMoney(item.value)} ${currency}`;
            }

            if (t === 'add') {
                return `${item.label}: +${formatMoney(item.delta)} ${currency}`;
            }

            if (t === 'multiply') {
                return `${item.label}: x${formatMoney(item.factor)}`;
            }

            if (t === 'boolean') {
                return `${item.label}: ${item.value ? 'Да' : 'Нет'} (${item.delta >= 0 ? '+' : ''}${formatMoney(item.delta)} ${currency})`;
            }

            if (t === 'select') {
                return `${item.label}: ${item.option_label || item.value} (+${formatMoney(item.price_delta)} ${currency}, x${formatMoney(item.multiplier)})`;
            }

            if (t === 'minimum_total') {
                return `Применен минимум: ${formatMoney(item.value)} ${currency}`;
            }

            if (t === 'maximum_total') {
                return `Применен максимум: ${formatMoney(item.value)} ${currency}`;
            }

            return `${t}: ${JSON.stringify(item)}`;
        }

        function setResult(total, currency, breakdown) {
            const listItems = Array.isArray(breakdown)
                ? breakdown.map(function (item) {
                    return `<li>${describeBreakdownItem(item, currency || '')}</li>`;
                }).join('')
                : '';

            resultEl.innerHTML = `
                <span class="d-block small text-muted mb-1">Итоговая стоимость</span>
                <span class="display-6 fw-semibold">${formatMoney(total)} ${currency || ''}</span>
                ${listItems ? `<ul class="calculator-breakdown mt-2 mb-0">${listItems}</ul>` : ''}
            `;
        }

        function renderSchemaFields(schema) {
            fieldsContainer.innerHTML = '';
            const row = document.createElement('div');
            row.className = 'row g-2';

            const fields = Array.isArray((schema || {}).fields) ? schema.fields : [];
            if (!fields.length) {
                const info = document.createElement('div');
                info.className = 'small text-muted';
                info.textContent = 'Для этой услуги нет дополнительных параметров.';
                fieldsContainer.appendChild(info);
                return;
            }

            fields.forEach(function (field) {
                if (!field || typeof field !== 'object' || !field.name) return;
                row.appendChild(createCalculatorField(field));
            });

            fieldsContainer.appendChild(row);
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

        async function loadServices() {
            setStatus('Загружаем каталог услуг...', false);

            try {
                const response = await fetch('/api/v1/catalog/services');
                if (!response.ok) {
                    throw new Error('catalog_load_failed');
                }

                services = await response.json();
                serviceSelect.innerHTML = '<option value="">Выберите услугу...</option>';

                services.forEach(function (service) {
                    const option = document.createElement('option');
                    option.value = String(service.id);
                    option.textContent = `${service.name} (${service.slug})`;
                    serviceSelect.appendChild(option);
                });

                setStatus(services.length ? 'Каталог загружен.' : 'Каталог пуст.', false);
            } catch (_err) {
                setStatus('Не удалось загрузить каталог услуг.', true);
            }
        }

        serviceSelect.addEventListener('change', function () {
            const serviceId = Number(serviceSelect.value || 0);
            const service = services.find(function (s) { return Number(s.id) === serviceId; });
            selectedSchema = (service && service.calculator_schema) || {};
            renderSchemaFields(selectedSchema);
            resultEl.innerHTML = '<span class="text-muted">Нажмите "Рассчитать" для получения стоимости.</span>';
        });

        form.addEventListener('submit', async function (event) {
            event.preventDefault();

            const serviceId = Number(serviceSelect.value || 0);
            if (!serviceId) {
                setStatus('Сначала выберите услугу.', true);
                return;
            }

            setStatus('Считаем...', false);

            try {
                const response = await fetch(`/api/v1/catalog/services/${serviceId}/calculate`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(collectPayload()),
                });

                if (!response.ok) {
                    throw new Error('calculate_failed');
                }

                const data = await response.json();
                if (!data.ok) {
                    throw new Error(data.error || 'calculate_failed');
                }

                setResult(
                    data.total,
                    data.currency || selectedSchema.currency || 'RUB',
                    data.breakdown || []
                );
                setStatus('Расчет выполнен.', false);
            } catch (_err) {
                setStatus('Ошибка расчета. Проверьте параметры и повторите.', true);
            }
        });

        loadServices();
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
        const rowBg = toRgba(getSelectedOptionColor(selectEl), 0.16);

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
            applyRowStatusColor(selectEl, toRgba(getSelectedOptionColor(selectEl), 0.16));
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
            applyRowStatusColor(selectEl, toRgba(getSelectedOptionColor(selectEl), 0.16));
        });
    }

    initStatusSelects(document);

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
