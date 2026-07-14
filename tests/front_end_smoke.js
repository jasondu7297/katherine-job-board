'use strict';

const fs = require('fs');
const vm = require('vm');

class FakeClassList {
  constructor() { this.values = new Set(); }
  add(value) { this.values.add(value); }
  remove(value) { this.values.delete(value); }
  toggle(value, force) {
    if (force === undefined) force = !this.values.has(value);
    if (force) this.values.add(value); else this.values.delete(value);
    return force;
  }
}

class FakeElement {
  constructor(id = '') {
    this.id = id;
    this.value = '';
    this.checked = false;
    this.hidden = false;
    this.innerHTML = '';
    this.textContent = '';
    this.href = '';
    this.children = [];
    this.listeners = {};
    this.dataset = {};
    this.attributes = {};
    this.classList = new FakeClassList();
    this.tagName = 'DIV';
    this.open = false;
  }
  addEventListener(type, callback) {
    (this.listeners[type] ||= []).push(callback);
  }
  appendChild(child) { this.children.push(child); return child; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  closest() { return null; }
  focus() { global.document.activeElement = this; }
  showModal() { this.open = true; }
  close() { this.open = false; }
}

const ids = [
  'heroRefreshDate', 'sourceHealth', 'statTotal', 'statCanada', 'statUS', 'statStrong',
  'searchInput', 'countryFilter', 'laneFilter', 'termFilter', 'eligibilityFilter',
  'modeFilter', 'sourceFilter', 'sortFilter', 'recentOnly', 'savedOnly', 'clearFilters',
  'resultCount', 'resultSummary', 'jobGrid', 'emptyState', 'emptyClear', 'loadMore',
  'emailDialog', 'emailTitle', 'emailContext', 'emailSubject', 'emailBody',
  'copyEmail', 'openMail', 'methodDialog', 'toast'
];

const elements = Object.fromEntries(ids.map((id) => [id, new FakeElement(id)]));
for (const id of ['countryFilter', 'laneFilter', 'termFilter', 'eligibilityFilter', 'modeFilter', 'sourceFilter']) {
  elements[id].value = 'all';
  elements[id].tagName = 'SELECT';
}
elements.sortFilter.value = 'fit';
elements.sortFilter.tagName = 'SELECT';
elements.searchInput.tagName = 'INPUT';
elements.emailSubject.tagName = 'INPUT';
elements.emailBody.tagName = 'TEXTAREA';
elements.emailDialog.tagName = 'DIALOG';
elements.methodDialog.tagName = 'DIALOG';

let domReady;
global.window = { isSecureContext: false };
Object.defineProperty(globalThis, 'navigator', { value: {}, configurable: true });
global.localStorage = {
  store: new Map(),
  getItem(key) { return this.store.has(key) ? this.store.get(key) : null; },
  setItem(key, value) { this.store.set(key, value); }
};
global.document = {
  activeElement: null,
  addEventListener(type, callback) {
    if (type === 'DOMContentLoaded') domReady = callback;
  },
  getElementById(id) { return elements[id] || null; },
  querySelectorAll(selector) {
    if (selector === 'dialog') return [elements.emailDialog, elements.methodDialog];
    return [];
  },
  createElement(tag) {
    const element = new FakeElement();
    element.tagName = tag.toUpperCase();
    return element;
  },
  execCommand() { return true; },
  body: { appendChild() {} }
};

global.setTimeout = setTimeout;
global.clearTimeout = clearTimeout;

vm.runInThisContext(fs.readFileSync('data/jobs.js', 'utf8'), { filename: 'data/jobs.js' });
vm.runInThisContext(fs.readFileSync('app.js', 'utf8'), { filename: 'app.js' });

(async () => {
  if (typeof domReady !== 'function') throw new Error('DOMContentLoaded handler was not registered.');
  await domReady();
  await new Promise((resolve) => setTimeout(resolve, 0));

  const refresh = window.JOB_BOARD_DATA.metadata?.refresh || {};
  const sourceHealth = elements.sourceHealth.textContent;

  if (refresh.offline) {
    if (!sourceHealth.includes('offline snapshot')) {
      throw new Error(`Expected offline source-health message; got ${sourceHealth}`);
    }
  } else {
    const expectedUnique = Number(
      refresh.dynamic_candidates_unique ?? refresh.dynamic_candidates ?? 0
    ).toLocaleString();
    const expectedCompleted = (
      Number(refresh.successful_sources || 0) + Number(refresh.empty_sources || 0)
    ).toLocaleString();
    const expectedFailed = Number(refresh.failed_sources || 0).toLocaleString();

    if (!sourceHealth.includes(`${expectedUnique} unique live opportunities`)) {
      throw new Error(`Live source-health message omitted the unique-job count; got ${sourceHealth}`);
    }
    if (!sourceHealth.includes(`${expectedCompleted} sources completed`)) {
      throw new Error(`Live source-health message omitted the completed-source count; got ${sourceHealth}`);
    }
    if (Number(refresh.failed_sources || 0) > 0 && !sourceHealth.includes(`${expectedFailed} failed`)) {
      throw new Error(`Live source-health message omitted the failed-source count; got ${sourceHealth}`);
    }
    if (Boolean(refresh.degraded) !== sourceHealth.includes('degraded coverage')) {
      throw new Error(`Live source-health degraded flag did not match the message; got ${sourceHealth}`);
    }
  }

  const expectedTotal = window.JOB_BOARD_DATA.jobs.length.toLocaleString();
  if (elements.statTotal.textContent !== expectedTotal) {
    throw new Error(`Expected ${expectedTotal} total jobs; got ${elements.statTotal.textContent}`);
  }
  const initialCards = (elements.jobGrid.innerHTML.match(/<article class="job-card/g) || []).length;
  const expectedInitialCards = Math.min(12, window.JOB_BOARD_DATA.jobs.length);
  if (initialCards !== expectedInitialCards) {
    throw new Error(`Expected ${expectedInitialCards} initially rendered cards; got ${initialCards}`);
  }
  const topCompany = window.JOB_BOARD_DATA.jobs[0].company;
  if (!elements.jobGrid.innerHTML.includes(topCompany)) throw new Error('Top ranked job was not rendered.');

  const searchTarget = window.JOB_BOARD_DATA.jobs.find(
    (job) => job.company && job.title && job.location
  );
  if (!searchTarget) throw new Error('No suitable job was available for the search smoke test.');
  elements.searchInput.value = `${searchTarget.company} ${searchTarget.title} ${searchTarget.location}`;
  for (const callback of elements.searchInput.listeners.input || []) callback({ target: elements.searchInput });
  const filteredCount = Number(elements.resultCount.textContent.replace(/,/g, ''));
  if (!Number.isFinite(filteredCount) || filteredCount < 1) {
    throw new Error(`Search filter expected at least one result; got ${elements.resultCount.textContent}`);
  }
  if (window.JOB_BOARD_DATA.jobs.length > 1 && filteredCount >= window.JOB_BOARD_DATA.jobs.length) {
    throw new Error('Search filter did not narrow the result set.');
  }

  elements.searchInput.value = '';
  for (const callback of elements.searchInput.listeners.input || []) callback({ target: elements.searchInput });
  const firstId = window.JOB_BOARD_DATA.jobs[0].id;
  const emailButton = {
    dataset: { action: 'email', jobId: firstId },
    closest(selector) { return selector === '[data-action]' ? this : null; }
  };
  for (const callback of elements.jobGrid.listeners.click || []) callback({ target: emailButton });
  if (!elements.emailDialog.open) throw new Error('Cold-email dialog did not open.');
  if (!elements.emailSubject.value.includes('BCom/JD candidate')) throw new Error('Cold-email subject was not populated.');
  if (!elements.emailBody.value.includes('15-minute conversation')) throw new Error('Cold-email body was not populated.');

  console.log('Front-end smoke test passed: rendering, search, and cold-email modal.');
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
