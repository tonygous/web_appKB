const overlay = document.getElementById('loading-overlay');
const errorBox = document.getElementById('error-box');
const progressText = document.getElementById('progress-text');
const diagnosticsContent = document.getElementById('diagnostics-content');
const diagnosticsPanel = document.getElementById('diagnostics-panel');
const crawlForm = document.getElementById('crawl-form');
const loadDiagnosticsBtn = document.getElementById('load-diagnostics');
const generateBtn = document.getElementById('generate-btn');
const summaryCard = document.getElementById('run-summary');
const summaryNote = document.getElementById('summary-note');
const summaryPages = document.getElementById('summary-pages');
const summaryErrors = document.getElementById('summary-errors');
const summarySkipped = document.getElementById('summary-skipped');
const loadingMessage = document.getElementById('loading-message');
const previewList = document.getElementById('preview-list');
const previewBtn = document.getElementById('preview-pages');
const previewSearchInput = document.getElementById('preview-search');
const previewHostFilter = document.getElementById('preview-host-filter');
const previewSelectAll = document.getElementById('preview-select-all');
const downloadSelectedBtn = document.getElementById('download-selected');
const previewTotal = document.getElementById('preview-total');
const previewSelected = document.getElementById('preview-selected');
const defaultLoadingMessage = 'Crawling… this can take up to 90 seconds.';
let pollTimer = null;
let previewPages = [];

const renderSummary = (data = {}, noteText = '') => {
    if (!summaryCard) return;
    const pages = data.pages_count ?? 0;
    const errors = Array.isArray(data.errors) ? data.errors.length : data.errors || 0;
    const skipped = data.skipped_links ?? 0;
    summaryPages.textContent = pages;
    summaryErrors.textContent = errors;
    summarySkipped.textContent = skipped;
    if (summaryNote) {
        const timedOut = Boolean(data.timed_out);
        summaryNote.textContent = noteText || (timedOut ? 'Timed out before finishing.' : 'Latest run ready.');
    }
};

const refreshSummary = async (noteText = '') => {
    try {
        const res = await fetch('/debug/last-run');
        if (!res.ok) return;
        const data = await res.json();
        renderSummary(data, noteText);
    } catch (err) {
        console.error('Failed to refresh summary', err);
    }
};

const setLoading = (isLoading, message = defaultLoadingMessage) => {
    const controls = document.querySelectorAll('input, button, select, textarea');
    controls.forEach((el) => {
        if (isLoading) {
            el.setAttribute('disabled', 'disabled');
        } else {
            el.removeAttribute('disabled');
        }
    });

    if (isLoading) {
        overlay.classList.add('visible');
        overlay.setAttribute('aria-hidden', 'false');
        if (loadingMessage) {
            loadingMessage.textContent = message;
        }
    } else {
        overlay.classList.remove('visible');
        overlay.setAttribute('aria-hidden', 'true');
        if (loadingMessage) {
            loadingMessage.textContent = defaultLoadingMessage;
        }
    }

    generateBtn.classList.toggle('loading', isLoading);
};

const showError = (message) => {
    if (!message) {
        errorBox.classList.remove('visible');
        errorBox.textContent = '';
        return;
    }
    errorBox.textContent = message;
    errorBox.classList.add('visible');
};

const pruneEmptyFormData = (formData) => {
    for (const [key, value] of Array.from(formData.entries())) {
        if (typeof value === 'string' && value.trim() === '') {
            formData.delete(key);
        }
    }
    return formData;
};

const parseFilename = (response, fallback = 'knowledgebase.md') => {
    const disposition = response.headers.get('Content-Disposition') || '';
    const match = disposition.match(/filename="?([^";]+)"?/i);
    return match ? match[1] : fallback;
};

const downloadBlob = async (response, fallbackFilename) => {
    const blob = await response.blob();
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = parseFilename(response, fallbackFilename || 'knowledgebase.md');
    document.body.appendChild(a);
    a.click();
    a.remove();
    window.URL.revokeObjectURL(url);
};

const normalizeHost = (host) => host || 'Unknown host';

const getFilteredPreviewPages = () => {
    const query = (previewSearchInput?.value || '').toLowerCase().trim();
    const hostFilter = previewHostFilter?.value || 'all';

    return previewPages.filter((page) => {
        const host = normalizeHost(page.host);
        const matchesHost = hostFilter === 'all' || host === hostFilter;
        const haystack = `${page.title || ''} ${page.path || ''} ${page.url || ''}`.toLowerCase();
        const matchesSearch = !query || haystack.includes(query);
        return matchesHost && matchesSearch;
    });
};

const renderHostFilterOptions = () => {
    if (!previewHostFilter) return;
    const hosts = Array.from(new Set(previewPages.map((page) => normalizeHost(page.host)))).sort();
    previewHostFilter.innerHTML = '<option value="all">All hosts</option>';
    hosts.forEach((host) => {
        const option = document.createElement('option');
        option.value = host;
        option.textContent = host;
        previewHostFilter.appendChild(option);
    });
};

const updatePreviewCounts = () => {
    const total = previewPages.length;
    const selectedCount = previewPages.filter((page) => page.selected).length;

    if (previewTotal) previewTotal.textContent = total;
    if (previewSelected) previewSelected.textContent = selectedCount;

    if (previewSelectAll) {
        previewSelectAll.checked = selectedCount > 0 && selectedCount === total;
        previewSelectAll.indeterminate = selectedCount > 0 && selectedCount < total;
    }

    if (downloadSelectedBtn) {
        downloadSelectedBtn.disabled = selectedCount === 0;
    }
};

const setHostSelection = (hostKey, checked) => {
    previewPages = previewPages.map((page) => ({
        ...page,
        selected: normalizeHost(page.host) === hostKey ? checked : page.selected,
    }));
    renderPreviewList();
};

const toggleSelectAll = (checked) => {
    previewPages = previewPages.map((page) => ({ ...page, selected: checked }));
    renderPreviewList();
};

const renderPreviewList = () => {
    if (!previewList) return;
    const filtered = getFilteredPreviewPages();
    previewList.innerHTML = '';

    if (!filtered.length) {
        previewList.innerHTML = '<p class="helper">No pages match your filters.</p>';
        updatePreviewCounts();
        return;
    }

    const grouped = filtered.reduce((acc, page) => {
        const host = normalizeHost(page.host);
        acc[host] = acc[host] || [];
        acc[host].push(page);
        return acc;
    }, {});

    Object.keys(grouped)
        .sort()
        .forEach((host) => {
            const group = document.createElement('div');
            group.className = 'preview__group';

            const header = document.createElement('div');
            header.className = 'preview__group-header';

            const title = document.createElement('div');
            const hostName = document.createElement('h3');
            hostName.textContent = host;
            const hostCount = document.createElement('p');
            hostCount.className = 'helper';
            hostCount.textContent = `${grouped[host].length} page(s)`;
            title.appendChild(hostName);
            title.appendChild(hostCount);

            const hostToggleLabel = document.createElement('label');
            hostToggleLabel.className = 'checkbox';
            const hostToggle = document.createElement('input');
            hostToggle.type = 'checkbox';
            const hostPages = grouped[host];
            const hostAllSelected = hostPages.every((page) => page.selected);
            const hostSomeSelected = hostPages.some((page) => page.selected);
            hostToggle.checked = hostAllSelected;
            hostToggle.indeterminate = !hostAllSelected && hostSomeSelected;
            hostToggle.addEventListener('change', (event) => {
                setHostSelection(host, event.target.checked);
            });
            hostToggleLabel.appendChild(hostToggle);
            const hostToggleText = document.createElement('span');
            hostToggleText.textContent = hostAllSelected ? 'Unselect host' : 'Select host';
            hostToggleLabel.appendChild(hostToggleText);

            header.appendChild(title);
            header.appendChild(hostToggleLabel);

            const rows = document.createElement('div');
            rows.className = 'preview__rows';

            hostPages.forEach((page) => {
                const row = document.createElement('div');
                row.className = 'preview__row';

                const checkbox = document.createElement('input');
                checkbox.type = 'checkbox';
                checkbox.checked = !!page.selected;
                checkbox.addEventListener('change', (event) => {
                    page.selected = event.target.checked;
                    renderPreviewList();
                });

                const info = document.createElement('div');
                const pageTitle = document.createElement('h3');
                pageTitle.textContent = page.title || page.path || page.url || 'Page';
                const path = document.createElement('p');
                path.textContent = page.path || page.url || '';
                const filename = document.createElement('p');
                filename.textContent = page.suggested_filename ? `File: ${page.suggested_filename}` : '';

                info.appendChild(pageTitle);
                if (path.textContent) info.appendChild(path);
                if (filename.textContent) info.appendChild(filename);

                row.appendChild(checkbox);
                row.appendChild(info);
                rows.appendChild(row);
            });

            group.appendChild(header);
            group.appendChild(rows);
            previewList.appendChild(group);
        });

    updatePreviewCounts();
};

const pollDiagnostics = () => {
    pollTimer = setInterval(async () => {
        try {
            const res = await fetch('/debug/last-run');
            if (!res.ok) return;
            const data = await res.json();
            const message = `Crawling… Pages: ${data.pages_count || 0}, Thin pages: ${data.thin_pages_count || 0}, Errors: ${data.errors?.length || 0}`;
            progressText.textContent = message;
            renderSummary(data, 'Crawling in progress…');
        } catch (err) {
            console.error('Diagnostics poll failed', err);
        }
    }, 2000);
};

const stopPolling = () => {
    if (pollTimer) {
        clearInterval(pollTimer);
        pollTimer = null;
    }
};

const handleSubmit = async (event) => {
    event.preventDefault();
    const submitter = event.submitter || generateBtn;
    const mode = submitter?.value === 'zip' ? 'zip' : 'combined';

    if (mode === 'zip') {
        await handleGenerateZip();
        return;
    }

    showError('');
    progressText.textContent = 'Crawling… this can take up to 90 seconds';
    renderSummary({ pages_count: 0, errors: [], skipped_links: 0, timed_out: false }, 'Starting crawl…');
    setLoading(true);
    pollDiagnostics();

    const payload = buildCrawlPayload();
    if (!payload.url) {
        stopPolling();
        setLoading(false);
        showError('Website URL is required.');
        return;
    }

    try {
        const response = await fetch('/generate', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(payload),
        });

        stopPolling();

        if (!response.ok) {
            let message = 'Unable to complete crawl.';
            try {
                const data = await response.json();
                if (data?.detail) {
                    if (typeof data.detail === 'string') {
                        message = data.detail;
                    } else if (data.detail.message) {
                        message = data.detail.message;
                    } else if (typeof data.detail === 'object') {
                        message = JSON.stringify(data.detail);
                    }
                }
            } catch (_) {
                // ignore JSON parse issues
            }
            showError(message);
            progressText.textContent = 'Crawl failed. Please review diagnostics.';
            await refreshSummary('Last attempt failed.');
            return;
        }

        await downloadBlob(response);
        progressText.textContent = 'Download ready. Check your files.';
        await refreshSummary('Download ready.');
    } catch (err) {
        console.error(err);
        showError('Network error: ' + err.message);
        progressText.textContent = 'Network error. Please try again.';
        await refreshSummary('Network error during crawl.');
    } finally {
        setLoading(false);
        stopPolling();
    }
};

const toListFlexible = (value) => {
    if (Array.isArray(value)) return value;
    if (typeof value !== 'string') return [];
    return value
        .split(/[, \n\r\t]+/)
        .map((s) => s.trim())
        .filter(Boolean);
};

const getCheckboxValue = (name, defaultValue) => {
    const input = crawlForm?.elements[name];
    if (!input) return defaultValue;
    if (input.type === 'checkbox') return input.checked;
    const value = input.value;
    if (value === undefined || value === null || value === '') return defaultValue;
    return value === 'true' || value === 'on' || value === '1';
};

const getNumberValue = (name, defaultValue) => {
    const input = crawlForm?.elements[name];
    if (!input) return defaultValue;
    const value = Number(input.value);
    return Number.isFinite(value) ? value : defaultValue;
};

const buildCrawlPayload = () => {
    const formData = pruneEmptyFormData(new FormData(crawlForm));

    return {
        url: (formData.get('url') || '').toString().trim(),
        max_pages: getNumberValue('max_pages', 10),
        max_depth: getNumberValue('max_depth', 3),
        allowed_hosts: toListFlexible(formData.get('allowed_hosts')),
        path_prefixes: toListFlexible(formData.get('path_prefixes')),
        include_subdomains: getCheckboxValue('include_subdomains', false),
        respect_robots: getCheckboxValue('respect_robots', true),
        use_sitemap: getCheckboxValue('use_sitemap', true),
        strip_links: getCheckboxValue('strip_links', true),
        strip_images: getCheckboxValue('strip_images', true),
        readability_fallback: getCheckboxValue('readability_fallback', true),
        min_text_chars: getNumberValue('min_text_chars', 600),
        render_mode: (formData.get('render_mode') || 'http').toString(),
    };
};

const buildDownloadPayload = (pages = []) => ({
    ...buildCrawlPayload(),
    pages,
});

const handlePreviewClick = async () => {
    if (!crawlForm) return;
    showError('');
    progressText.textContent = 'Preparing preview…';
    setLoading(true, 'Previewing pages…');

    const payload = buildCrawlPayload();
    if (!payload.url) {
        showError('Website URL is required for preview.');
        setLoading(false);
        return;
    }

    try {
        const response = await fetch('/crawl-preview', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(payload),
        });

        if (!response.ok) {
            let message = 'Unable to load preview.';
            try {
                const data = await response.json();
                if (data?.detail) {
                    message = typeof data.detail === 'string' ? data.detail : data.detail.message || message;
                }
            } catch (_) {
                // ignore JSON parse issues
            }
            showError(message);
            if (previewList) {
                previewList.innerHTML = `<p class="helper">${message}</p>`;
            }
            progressText.textContent = 'Preview failed.';
            previewPages = [];
            updatePreviewCounts();
            return;
        }

        const data = await response.json();
        if (!Array.isArray(data)) {
            throw new Error('Unexpected preview response.');
        }

        previewPages = data.map((page) => ({ ...page, selected: true }));
        renderHostFilterOptions();
        renderPreviewList();
        progressText.textContent = `Preview ready. Found ${previewPages.length} pages.`;
    } catch (err) {
        console.error('Preview failed', err);
        showError('Unable to load preview: ' + err.message);
        if (previewList) {
            previewList.innerHTML = '<p class="helper">Preview failed. Please try again.</p>';
        }
        progressText.textContent = 'Preview failed.';
        previewPages = [];
        updatePreviewCounts();
    } finally {
        setLoading(false);
    }
};

const handleDownloadSelected = async () => {
    const selectedPages = previewPages
        .filter((page) => page.selected)
        .map((page) => ({
            url: page.url,
            host: page.host,
            path: page.path,
            title: page.title,
            suggested_filename: page.suggested_filename,
        }));

    if (!selectedPages.length) {
        showError('Select at least one page to download.');
        return;
    }

    const payload = buildDownloadPayload(selectedPages);
    if (!payload.url) {
        showError('Please provide a URL before downloading.');
        return;
    }

    showError('');
    progressText.textContent = 'Downloading selected pages…';
    setLoading(true, 'Downloading selected pages…');

    try {
        const response = await fetch('/download-selected', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(payload),
        });

        if (!response.ok) {
            let message = 'Unable to download selection.';
            try {
                const data = await response.json();
                if (data?.detail) {
                    message = typeof data.detail === 'string' ? data.detail : data.detail.message || message;
                }
            } catch (_) {
                // ignore JSON parse issues
            }
            showError(message);
            progressText.textContent = 'Download failed. Please review errors and try again.';
            return;
        }

        await downloadBlob(response, 'knowledgebase_pages.zip');
        progressText.textContent = 'Download ready. Check your files.';
        await refreshSummary('Download ready.');
    } catch (err) {
        console.error('Download failed', err);
        showError('Unable to download selection: ' + err.message);
        progressText.textContent = 'Download failed. Please try again.';
    } finally {
        setLoading(false);
    }
};

const handleGenerateZip = async () => {
    showError('');
    progressText.textContent = 'Generating ZIP… this may take up to 90 seconds';
    setLoading(true, 'Generating ZIP…');

    const crawlPayload = buildCrawlPayload();
    if (!crawlPayload.url) {
        showError('Website URL is required to generate ZIP.');
        setLoading(false);
        return;
    }

    try {
        const previewResponse = await fetch('/crawl-preview', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(crawlPayload),
        });

        if (!previewResponse.ok) {
            let message = 'Unable to prepare ZIP.';
            try {
                const data = await previewResponse.json();
                if (data?.detail) {
                    message = typeof data.detail === 'string' ? data.detail : data.detail.message || message;
                }
            } catch (_) {
                // ignore JSON parse issues
            }
            showError(message);
            progressText.textContent = 'ZIP generation failed during preview.';
            return;
        }

        const previewData = await previewResponse.json();
        if (!Array.isArray(previewData) || previewData.length === 0) {
            showError('No pages found to include in ZIP.');
            progressText.textContent = 'ZIP generation failed: no pages found.';
            previewPages = [];
            renderPreviewList();
            return;
        }

        previewPages = previewData.map((page) => ({ ...page, selected: true }));
        renderHostFilterOptions();
        renderPreviewList();

        const downloadPayload = buildDownloadPayload(
            previewPages.map((page) => ({
                url: page.url,
                host: page.host,
                path: page.path,
                title: page.title,
                suggested_filename: page.suggested_filename,
            }))
        );

        const downloadResponse = await fetch('/download-selected', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(downloadPayload),
        });

        if (!downloadResponse.ok) {
            let message = 'Unable to download ZIP.';
            try {
                const data = await downloadResponse.json();
                if (data?.detail) {
                    message = typeof data.detail === 'string' ? data.detail : data.detail.message || message;
                }
            } catch (_) {
                // ignore JSON parse issues
            }
            showError(message);
            progressText.textContent = 'ZIP download failed. Please review errors and try again.';
            return;
        }

        await downloadBlob(downloadResponse, 'knowledgebase_pages.zip');
        progressText.textContent = 'ZIP ready. Check your files.';
        await refreshSummary('ZIP ready.');
    } catch (err) {
        console.error('ZIP generation failed', err);
        showError('Unable to generate ZIP: ' + err.message);
        progressText.textContent = 'ZIP generation failed. Please try again.';
    } finally {
        setLoading(false);
    }
};

const renderDiagnostics = (data) => {
    diagnosticsContent.innerHTML = '';
    const summary = document.createElement('div');
    summary.className = 'stack';
    summary.innerHTML = `
        <p class="helper">Pages: <strong>${data.pages_count || 0}</strong> · Thin pages: <strong>${data.thin_pages_count || 0}</strong></p>
        <p class="helper">Errors: <strong>${data.errors?.length || 0}</strong></p>
    `;
    diagnosticsContent.appendChild(summary);

    const diagList = document.createElement('ul');
    diagList.className = 'helper';

    (data.diagnostics || []).slice(0, 10).forEach((item) => {
        const li = document.createElement('li');
        li.textContent = item;
        diagList.appendChild(li);
    });

    if (diagList.childElementCount === 0) {
        const li = document.createElement('li');
        li.textContent = 'No diagnostics available.';
        diagList.appendChild(li);
    }

    diagnosticsContent.appendChild(diagList);

    const errors = document.createElement('ul');
    errors.className = 'helper';
    (data.errors || []).slice(0, 10).forEach((item) => {
        const li = document.createElement('li');
        li.textContent = item;
        errors.appendChild(li);
    });

    if (errors.childElementCount > 0) {
        const header = document.createElement('p');
        header.className = 'helper';
        header.textContent = 'Errors:';
        diagnosticsContent.appendChild(header);
        diagnosticsContent.appendChild(errors);
    }
};

const loadDiagnostics = async () => {
    try {
        const response = await fetch('/debug/last-run');
        if (!response.ok) {
            diagnosticsContent.innerHTML = '<p class="helper">No diagnostics available.</p>';
            return;
        }
        const data = await response.json();
        renderDiagnostics(data);
        diagnosticsPanel.open = true;
    } catch (err) {
        diagnosticsContent.innerHTML = '<p class="helper">Unable to load diagnostics.</p>';
    }
};

crawlForm?.addEventListener('submit', handleSubmit);
loadDiagnosticsBtn?.addEventListener('click', loadDiagnostics);
previewBtn?.addEventListener('click', handlePreviewClick);
downloadSelectedBtn?.addEventListener('click', handleDownloadSelected);
previewSearchInput?.addEventListener('input', renderPreviewList);
previewHostFilter?.addEventListener('change', renderPreviewList);
previewSelectAll?.addEventListener('change', (event) => toggleSelectAll(event.target.checked));
refreshSummary('Awaiting first run.');
updatePreviewCounts();
