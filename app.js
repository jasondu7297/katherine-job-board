(() => {
  'use strict';

  const PAGE_SIZE = 12;
  const STORAGE_KEYS = {
    saved: 'kd-job-board-saved-v1',
    statuses: 'kd-job-board-statuses-v1'
  };

  const state = {
    data: null,
    jobs: [],
    filtered: [],
    visible: PAGE_SIZE,
    saved: new Set(readStorage(STORAGE_KEYS.saved, [])),
    statuses: readStorage(STORAGE_KEYS.statuses, {}),
    currentEmailJob: null
  };

  const el = {};

  document.addEventListener('DOMContentLoaded', init);

  async function init() {
    cacheElements();
    bindStaticEvents();

    try {
      state.data = await loadBoardData();
      state.jobs = Array.isArray(state.data?.jobs) ? state.data.jobs : [];
      populateFilters();
      updateHeaderAndStats();
      applyFilters();
    } catch (error) {
      console.error(error);
      el.jobGrid.innerHTML = `
        <div class="empty-state" style="display:block;grid-column:1/-1">
          <div aria-hidden="true">!</div>
          <h3>The job data could not be loaded</h3>
          <p>Open this project through a local web server or confirm that <code>data/jobs.js</code> is present.</p>
        </div>`;
      el.resultSummary.textContent = 'Data load failed.';
    }
  }

  function cacheElements() {
    const ids = [
      'heroRefreshDate', 'sourceHealth', 'statTotal', 'statCanada', 'statUS', 'statStrong',
      'searchInput', 'countryFilter', 'laneFilter', 'termFilter', 'eligibilityFilter',
      'modeFilter', 'sourceFilter', 'sortFilter', 'recentOnly', 'savedOnly', 'clearFilters',
      'resultCount', 'resultSummary', 'jobGrid', 'emptyState', 'emptyClear', 'loadMore',
      'emailDialog', 'emailTitle', 'emailContext', 'emailSubject', 'emailBody',
      'copyEmail', 'openMail', 'methodDialog', 'toast'
    ];
    ids.forEach((id) => { el[id] = document.getElementById(id); });
  }

  async function loadBoardData() {
    if (window.JOB_BOARD_DATA?.jobs) return window.JOB_BOARD_DATA;
    const response = await fetch('data/jobs.json', { cache: 'no-store' });
    if (!response.ok) throw new Error(`Could not load jobs.json (${response.status})`);
    return response.json();
  }

  function bindStaticEvents() {
    const filterElements = [
      el.countryFilter, el.laneFilter, el.termFilter, el.eligibilityFilter,
      el.modeFilter, el.sourceFilter, el.sortFilter, el.recentOnly, el.savedOnly
    ];

    el.searchInput.addEventListener('input', () => {
      state.visible = PAGE_SIZE;
      applyFilters();
    });
    filterElements.forEach((node) => node.addEventListener('change', () => {
      state.visible = PAGE_SIZE;
      applyFilters();
    }));

    el.clearFilters.addEventListener('click', resetFilters);
    el.emptyClear.addEventListener('click', resetFilters);
    el.loadMore.addEventListener('click', () => {
      state.visible += PAGE_SIZE;
      renderJobs();
    });

    el.jobGrid.addEventListener('click', handleGridClick);
    el.jobGrid.addEventListener('change', handleGridChange);

    document.querySelectorAll('[data-open-method]').forEach((button) => {
      button.addEventListener('click', () => openDialog(el.methodDialog));
    });
    document.querySelectorAll('[data-close-dialog]').forEach((button) => {
      button.addEventListener('click', () => button.closest('dialog')?.close());
    });
    document.querySelectorAll('dialog').forEach((dialog) => {
      dialog.addEventListener('click', (event) => {
        if (event.target === dialog) dialog.close();
      });
    });

    el.copyEmail.addEventListener('click', copyCurrentEmail);
    el.emailSubject.addEventListener('input', updateMailLink);
    el.emailBody.addEventListener('input', updateMailLink);

    document.addEventListener('keydown', (event) => {
      const tag = document.activeElement?.tagName?.toLowerCase();
      const isEditing = tag === 'input' || tag === 'textarea' || tag === 'select';
      if (event.key === '/' && !isEditing) {
        event.preventDefault();
        el.searchInput.focus();
      }
    });
  }

  function populateFilters() {
    const lanes = uniqueSorted(state.jobs.map((job) => job.lane));
    const terms = uniqueSorted(state.jobs.map((job) => job.term));
    const modes = uniqueSorted(state.jobs.map((job) => job.mode));
    appendOptions(el.laneFilter, lanes);
    appendOptions(el.termFilter, terms);
    appendOptions(el.modeFilter, modes);
  }

  function appendOptions(select, values) {
    values.forEach((value) => {
      const option = document.createElement('option');
      option.value = value;
      option.textContent = value;
      select.appendChild(option);
    });
  }

  function uniqueSorted(values) {
    return [...new Set(values.filter(Boolean))].sort((a, b) => a.localeCompare(b));
  }

  function updateHeaderAndStats() {
    const generatedAt = state.data?.metadata?.generated_at;
    el.heroRefreshDate.textContent = generatedAt
      ? formatDateTime(generatedAt)
      : 'date unavailable';

    const refresh = state.data?.metadata?.refresh || {};
    if (refresh.offline) {
      el.sourceHealth.textContent = 'Reviewed offline snapshot · live daily refresh starts after GitHub deployment.';
    } else {
      const completed = Number(refresh.successful_sources || 0) + Number(refresh.empty_sources || 0);
      const failed = Number(refresh.failed_sources || 0);
      const unique = Number(refresh.dynamic_candidates_unique ?? refresh.dynamic_candidates ?? 0);
      const parts = [`${unique.toLocaleString()} unique live opportunities`, `${completed.toLocaleString()} sources completed`];
      if (failed) parts.push(`${failed.toLocaleString()} failed`);
      if (refresh.degraded) parts.push('degraded coverage');
      el.sourceHealth.textContent = `${parts.join(' · ')}.`;
    }

    el.statTotal.textContent = state.jobs.length.toLocaleString();
    el.statCanada.textContent = state.jobs.filter((job) => job.country === 'Canada').length.toLocaleString();
    el.statUS.textContent = state.jobs.filter((job) => job.country === 'United States').length.toLocaleString();
    el.statStrong.textContent = state.jobs.filter((job) => Number(job.fit_score) >= 90).length.toLocaleString();
  }

  function applyFilters() {
    const query = normalize(el.searchInput.value);
    const generatedDate = new Date(state.data?.metadata?.generated_at || Date.now());

    state.filtered = state.jobs.filter((job) => {
      const haystack = normalize([
        job.company, job.title, job.location, job.country, job.lane, job.term,
        job.description, job.interest, job.eligibility_notes, job.source_name,
        job.source_family, job.provenance, ...(job.skills || []), ...(job.fit_reasons || [])
      ].join(' '));

      if (query && !haystack.includes(query)) return false;
      if (el.countryFilter.value !== 'all' && job.country !== el.countryFilter.value) return false;
      if (el.laneFilter.value !== 'all' && job.lane !== el.laneFilter.value) return false;
      if (el.termFilter.value !== 'all' && job.term !== el.termFilter.value) return false;
      if (el.eligibilityFilter.value !== 'all' && job.eligibility_status !== el.eligibilityFilter.value) return false;
      if (el.modeFilter.value !== 'all' && job.mode !== el.modeFilter.value) return false;
      const government = isGovernmentSource(job);
      if (el.sourceFilter.value === 'government' && !government) return false;
      if (el.sourceFilter.value === 'official' && !job.official) return false;
      if (el.sourceFilter.value === 'discovery' && job.official) return false;
      if (el.savedOnly.checked && !state.saved.has(job.id)) return false;
      if (el.recentOnly.checked && daysBetween(job.posted_date, generatedDate) > 7) return false;
      return true;
    });

    sortJobs(state.filtered, el.sortFilter.value);
    renderJobs();
  }

  function sortJobs(jobs, mode) {
    const deadlineTime = (job) => job.deadline ? new Date(`${job.deadline}T12:00:00Z`).getTime() : Number.MAX_SAFE_INTEGER;
    const postedTime = (job) => new Date(`${job.posted_date}T12:00:00Z`).getTime() || 0;

    jobs.sort((a, b) => {
      if (mode === 'newest') return postedTime(b) - postedTime(a) || b.fit_score - a.fit_score;
      if (mode === 'deadline') return deadlineTime(a) - deadlineTime(b) || b.fit_score - a.fit_score;
      if (mode === 'company') return a.company.localeCompare(b.company) || b.fit_score - a.fit_score;
      return b.fit_score - a.fit_score || postedTime(b) - postedTime(a);
    });
  }

  function renderJobs() {
    const rendered = state.filtered.slice(0, state.visible);
    el.resultCount.textContent = state.filtered.length.toLocaleString();
    el.resultSummary.textContent = buildResultSummary();
    el.jobGrid.innerHTML = rendered.map(renderCard).join('');

    const empty = state.filtered.length === 0;
    el.emptyState.hidden = !empty;
    el.jobGrid.hidden = empty;
    el.loadMore.hidden = empty || state.visible >= state.filtered.length;
    if (!el.loadMore.hidden) {
      const remaining = state.filtered.length - state.visible;
      el.loadMore.textContent = `Show ${Math.min(PAGE_SIZE, remaining)} more opportunities`;
    }
  }

  function buildResultSummary() {
    const eligible = state.filtered.filter((job) => job.eligibility_status === 'likely').length;
    const checks = state.filtered.filter((job) => job.eligibility_status === 'check').length;
    const saved = state.filtered.filter((job) => state.saved.has(job.id)).length;
    const government = state.filtered.filter(isGovernmentSource).length;
    const pieces = [];
    if (eligible) pieces.push(`${eligible} likely match${eligible === 1 ? '' : 'es'}`);
    if (checks) pieces.push(`${checks} need requirement checks`);
    if (government) pieces.push(`${government} government`);
    if (saved) pieces.push(`${saved} saved`);
    return pieces.length ? pieces.join(' · ') : 'No matching roles in the current view.';
  }

  function renderCard(job) {
    const saved = state.saved.has(job.id);
    const generatedDate = new Date(state.data?.metadata?.generated_at || Date.now());
    const recent = daysBetween(job.posted_date, generatedDate) <= 7;
    const status = state.statuses[job.id] || 'Not started';
    const initials = companyInitials(job.company);
    const safeLink = safeUrl(job.url);
    const deadline = job.deadline ? `<span>${icon('calendar')} ${escapeHTML(job.deadline_label)}</span>` : '';
    const compensation = job.compensation ? `<span>${icon('money')} ${escapeHTML(job.compensation)}</span>` : '';
    const fitReasons = (job.fit_reasons || []).slice(0, 3).map((reason) => `<li>${escapeHTML(reason)}</li>`).join('');
    const skills = (job.skills || []).slice(0, 5).map((skill) => `<span>${escapeHTML(skill)}</span>`).join('');
    const badges = [
      `<span class="badge badge-lane">${escapeHTML(job.lane)}</span>`,
      `<span class="badge badge-mode">${escapeHTML(job.mode)}</span>`,
      recent ? '<span class="badge badge-new">Recent</span>' : '',
      isGovernmentSource(job) ? '<span class="badge badge-official">Government</span>' : '',
      job.official ? '<span class="badge badge-official">Official source</span>' : '<span class="badge badge-mode">Discovery lead</span>'
    ].join('');

    return `
      <article class="job-card ${saved ? 'is-saved' : ''}" data-job-card="${escapeAttr(job.id)}">
        <div class="job-card-main">
          <div class="card-top">
            <div class="company-lockup">
              <div class="company-mark" aria-hidden="true">${escapeHTML(initials)}</div>
              <div>
                <div class="company-name" title="${escapeAttr(job.company)}">${escapeHTML(job.company)}</div>
                <h3 class="job-title">${escapeHTML(job.title)}</h3>
              </div>
            </div>
            <button class="save-button ${saved ? 'active' : ''}" type="button" data-action="save" data-job-id="${escapeAttr(job.id)}" aria-pressed="${saved}" aria-label="${saved ? 'Remove from saved roles' : 'Save role'}">
              ${icon('bookmark')}
            </button>
          </div>

          <div class="meta-row">
            <span>${icon('location')} ${escapeHTML(job.location)}</span>
            <span>${icon('clock')} ${escapeHTML(job.term)}</span>
            <span>${icon('spark')} ${escapeHTML(job.posted_label)}</span>
            ${deadline}
            ${compensation}
          </div>

          <div class="badge-row">${badges}</div>

          <div class="fit-block">
            <div class="fit-score" style="--score:${Number(job.fit_score) || 0}" aria-label="Résumé fit score ${Number(job.fit_score) || 0} out of 100">
              <strong>${Number(job.fit_score) || 0}</strong>
            </div>
            <div class="fit-copy">
              <strong>${escapeHTML(job.fit_label)}</strong>
              <ul>${fitReasons}</ul>
            </div>
          </div>

          <div class="eligibility-box ${escapeAttr(job.eligibility_status)}">
            <div class="eligibility-head">
              <strong>${escapeHTML(job.eligibility_label)}</strong>
              <span>${escapeHTML(job.work_auth)}</span>
            </div>
            <p>${escapeHTML(job.eligibility_notes)}</p>
          </div>

          <p class="role-summary">${escapeHTML(job.description || job.interest)}</p>
          <div class="skill-row">${skills}</div>
          <div class="source-line">
            <span>Verified via</span>
            <a href="${escapeAttr(safeLink)}" target="_blank" rel="noopener noreferrer">${escapeHTML(job.source_name)}</a>
            <span>· ${job.official ? 'first-party' : 'discovery lead'} · ${formatVerification(job.verified_at)}</span>
          </div>
        </div>

        <div class="card-footer">
          <div class="status-wrap">
            <label for="status-${escapeAttr(job.id)}">Application status</label>
            <select id="status-${escapeAttr(job.id)}" data-action="status" data-job-id="${escapeAttr(job.id)}" aria-label="Application status for ${escapeAttr(job.company)} ${escapeAttr(job.title)}">
              ${statusOptions(status)}
            </select>
          </div>
          <button class="button card-action email-action" type="button" data-action="email" data-job-id="${escapeAttr(job.id)}">Cold email</button>
          <a class="button card-action apply-action" href="${escapeAttr(safeLink)}" target="_blank" rel="noopener noreferrer">View posting ↗</a>
        </div>
      </article>`;
  }

  function statusOptions(selected) {
    return ['Not started', 'Networking', 'Applied', 'Interview', 'Offer', 'Closed / passed']
      .map((status) => `<option value="${escapeAttr(status)}" ${status === selected ? 'selected' : ''}>${escapeHTML(status)}</option>`)
      .join('');
  }

  function handleGridClick(event) {
    const trigger = event.target.closest('[data-action]');
    if (!trigger) return;
    const id = trigger.dataset.jobId;
    const job = state.jobs.find((item) => item.id === id);
    if (!job) return;

    if (trigger.dataset.action === 'save') {
      toggleSaved(job, trigger);
    } else if (trigger.dataset.action === 'email') {
      openEmail(job);
    }
  }

  function handleGridChange(event) {
    const select = event.target.closest('select[data-action="status"]');
    if (!select) return;
    state.statuses[select.dataset.jobId] = select.value;
    writeStorage(STORAGE_KEYS.statuses, state.statuses);
    showToast(`Status updated to “${select.value}”.`);
  }

  function toggleSaved(job, button) {
    if (state.saved.has(job.id)) {
      state.saved.delete(job.id);
      showToast('Removed from saved roles.');
    } else {
      state.saved.add(job.id);
      showToast('Saved to your shortlist.');
    }
    writeStorage(STORAGE_KEYS.saved, [...state.saved]);

    if (el.savedOnly.checked) {
      applyFilters();
      return;
    }

    const card = button.closest('.job-card');
    const active = state.saved.has(job.id);
    button.classList.toggle('active', active);
    button.setAttribute('aria-pressed', String(active));
    button.setAttribute('aria-label', active ? 'Remove from saved roles' : 'Save role');
    card?.classList.toggle('is-saved', active);
    el.resultSummary.textContent = buildResultSummary();
  }

  function openEmail(job) {
    state.currentEmailJob = job;
    el.emailTitle.textContent = `${job.company} outreach`;
    el.emailContext.textContent = `${job.title} · ${job.location} · ${job.eligibility_label}`;
    el.emailSubject.value = job.email?.subject || '';
    el.emailBody.value = job.email?.body || '';
    updateMailLink();
    openDialog(el.emailDialog);
  }

  function updateMailLink() {
    if (!state.currentEmailJob) return;
    const recipient = state.currentEmailJob.contact_email || '';
    const params = new URLSearchParams({
      subject: el.emailSubject.value,
      body: el.emailBody.value
    });
    el.openMail.href = `mailto:${encodeURIComponent(recipient)}?${params.toString()}`;
  }

  async function copyCurrentEmail() {
    const text = `Subject: ${el.emailSubject.value}\n\n${el.emailBody.value}`;
    const ok = await copyText(text);
    showToast(ok ? 'Cold email copied.' : 'Copy failed—select the text manually.');
  }

  function openDialog(dialog) {
    if (!dialog) return;
    if (typeof dialog.showModal === 'function') dialog.showModal();
    else dialog.setAttribute('open', '');
  }

  function resetFilters() {
    el.searchInput.value = '';
    el.countryFilter.value = 'all';
    el.laneFilter.value = 'all';
    el.termFilter.value = 'all';
    el.eligibilityFilter.value = 'all';
    el.modeFilter.value = 'all';
    el.sourceFilter.value = 'all';
    el.sortFilter.value = 'fit';
    el.recentOnly.checked = false;
    el.savedOnly.checked = false;
    state.visible = PAGE_SIZE;
    applyFilters();
  }

  function isGovernmentSource(job) {
    const family = String(job?.source_family || '');
    const type = String(job?.source_type || '');
    return family.startsWith('government_') || type.includes('government');
  }

  function companyInitials(company) {
    const cleaned = company.replace(/[/&].*$/, '').trim();
    const parts = cleaned.split(/\s+/).filter(Boolean);
    if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
    return (parts[0][0] + parts[1][0]).toUpperCase();
  }

  function formatVerification(value) {
    if (!value) return 'date unavailable';
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString('en-CA', { month: 'short', day: 'numeric', year: 'numeric' });
  }

  function formatDateTime(value) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    return date.toLocaleString('en-CA', {
      month: 'long', day: 'numeric', year: 'numeric',
      hour: 'numeric', minute: '2-digit', timeZoneName: 'short',
      timeZone: 'America/Los_Angeles'
    });
  }

  function daysBetween(dateString, endDate) {
    const start = new Date(`${dateString}T12:00:00Z`);
    if (Number.isNaN(start.getTime())) return 9999;
    return Math.max(0, Math.floor((endDate.getTime() - start.getTime()) / 86400000));
  }

  function normalize(value) {
    return String(value || '')
      .normalize('NFD')
      .replace(/[\u0300-\u036f]/g, '')
      .toLowerCase()
      .trim();
  }

  function safeUrl(value) {
    try {
      const url = new URL(value);
      return ['http:', 'https:'].includes(url.protocol) ? url.href : '#';
    } catch {
      return '#';
    }
  }

  function escapeHTML(value) {
    return String(value ?? '').replace(/[&<>'"]/g, (char) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
    })[char]);
  }

  function escapeAttr(value) {
    return escapeHTML(value).replace(/`/g, '&#96;');
  }

  function icon(name) {
    const paths = {
      location: '<path d="M20 10c0 5-8 11-8 11S4 15 4 10a8 8 0 1 1 16 0Z"/><circle cx="12" cy="10" r="2.5"/>',
      clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
      calendar: '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M7 3v4m10-4v4M3 10h18"/>',
      money: '<path d="M4 7h16v10H4z"/><path d="M8 7c0 2-2 3-4 3m12-3c0 2 2 3 4 3M8 17c0-2-2-3-4-3m12 3c0-2 2-3 4-3"/><circle cx="12" cy="12" r="2"/>',
      spark: '<path d="m12 3 1.3 4.2L17 9l-3.7 1.8L12 15l-1.3-4.2L7 9l3.7-1.8L12 3Z"/><path d="m18.5 15 .7 2.1L21 18l-1.8.9-.7 2.1-.7-2.1L16 18l1.8-.9.7-2.1Z"/>',
      bookmark: '<path d="M6 4.8A1.8 1.8 0 0 1 7.8 3h8.4A1.8 1.8 0 0 1 18 4.8V21l-6-3.6L6 21V4.8Z"/>'
    };
    return `<svg aria-hidden="true" viewBox="0 0 24 24">${paths[name] || ''}</svg>`;
  }

  function readStorage(key, fallback) {
    try {
      const raw = localStorage.getItem(key);
      return raw ? JSON.parse(raw) : fallback;
    } catch {
      return fallback;
    }
  }

  function writeStorage(key, value) {
    try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* storage may be unavailable */ }
  }

  async function copyText(text) {
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(text);
        return true;
      }
      const area = document.createElement('textarea');
      area.value = text;
      area.style.position = 'fixed';
      area.style.opacity = '0';
      document.body.appendChild(area);
      area.focus();
      area.select();
      const copied = document.execCommand('copy');
      area.remove();
      return copied;
    } catch {
      return false;
    }
  }

  let toastTimer;
  function showToast(message) {
    clearTimeout(toastTimer);
    el.toast.textContent = message;
    el.toast.classList.add('show');
    toastTimer = setTimeout(() => el.toast.classList.remove('show'), 2300);
  }
})();
