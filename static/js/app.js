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
