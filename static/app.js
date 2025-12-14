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
let pollTimer = null;

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

const setLoading = (isLoading) => {
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
    } else {
        overlay.classList.remove('visible');
        overlay.setAttribute('aria-hidden', 'true');
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

const parseFilename = (response) => {
    const disposition = response.headers.get('Content-Disposition') || '';
    const match = disposition.match(/filename="?([^";]+)"?/i);
    return match ? match[1] : 'knowledgebase.md';
};

const downloadBlob = async (response) => {
    const blob = await response.blob();
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = parseFilename(response);
    document.body.appendChild(a);
    a.click();
    a.remove();
    window.URL.revokeObjectURL(url);
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

    showError('');
    progressText.textContent = 'Crawling… this can take up to 90 seconds';
    renderSummary({ pages_count: 0, errors: [], skipped_links: 0, timed_out: false }, 'Starting crawl…');
    setLoading(true);
    pollDiagnostics();

    const formData = new FormData(crawlForm);
    if (mode === 'zip') {
        formData.set('mode', 'zip');
    } else {
        formData.delete('mode');
    }

    // Remove empty fields to prevent parsing errors with empty multipart parts
    for (const [key, value] of Array.from(formData.entries())) {
        if (typeof value === 'string' && value.trim() === '') {
            formData.delete(key);
        }
    }

    try {
        const response = await fetch('/generate', {
            method: 'POST',
            body: formData,
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
refreshSummary('Awaiting first run.');
