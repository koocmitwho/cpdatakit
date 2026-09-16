import {setupFields, showSliceResult} from './fields.js';
import {setupAuthoring} from './authoring.js';

const csrf = document.querySelector('[name="csrf_token"]')?.value;
const projectId = document.body.dataset.projectId;
const schema = document.querySelector('#schema');
const dataset = document.querySelector('#dataset');
const resourceKinds = ['datasets', 'artifacts', 'schemas', 'jobs'];
const resourceState = JSON.parse(document.querySelector('#project-resources')?.textContent || 'null');
const offsets = Object.fromEntries(resourceKinds.map(kind => [kind, resourceState?.[kind]?.length || 0]));
const pageEpochs = Object.fromEntries(resourceKinds.map(kind => [kind, 0]));
const watching = new Set();
const terminal = status => ['succeeded', 'failed', 'cancelled'].includes(status);
let pendingRefresh;
let refreshEpoch = 0;

async function request(url, data) {
  const response = await fetch(url, data ? {
    method: 'POST', headers: {'X-CSRF-Token': csrf}, body: data,
  } : {});
  const result = await response.json();
  if (!response.ok) {
    const error = new Error(result.error?.message || result.detail || 'Request failed.');
    error.payload = result;
    throw error;
  }
  return result;
}

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function showResult(title, payload) {
  document.querySelector('#operation-result').hidden = false;
  document.querySelector('#result-title').textContent = title;
  const content = document.querySelector('#result-content');
  content.replaceChildren();
  const value = payload.value || payload;
  const validation = value.validation || value.report?.validation;
  if (payload.error) {
    content.append(element('p', typeof payload.error === 'string' ? payload.error : payload.error.message, 'error'));
    if (payload.error.action) content.append(element('p', payload.error.action));
  }
  if (value.message) content.append(element('p', value.message));
  if (validation) {
    content.append(element('p', validation.valid ? 'Validation passed' : 'Validation failed',
                           validation.valid ? 'success' : 'error'));
    for (const issue of [...validation.errors, ...validation.warnings]) {
      content.append(element('p', `${issue.field || 'Dataset'}: ${issue.message}`));
    }
  }
  if (value.file) content.append(element('p', `${value.file.filename} · ${value.file.format}`));
  const dimensions = value.dimensions || value.summary?.dimensions;
  if (dimensions && Object.keys(dimensions).length) {
    content.append(element('p', `Dimensions: ${Object.entries(dimensions).map(([k, v]) => `${k} = ${v}`).join(', ')}`));
  }
  if (value.record_count !== undefined) content.append(element('p', `Records: ${value.record_count}`));
  const details = element('details');
  details.append(element('summary', 'Details'), element('pre', JSON.stringify(payload, null, 2)));
  content.append(details);
}

function pageUrl(offset = 0) {
  return `/api/projects/${projectId}?limit=50&offset=${offset}&newest_first=true`;
}

function uniqueRecords(items) {
  return [...new Map(items.map(item => [String(item.id), item])).values()];
}

function updatePaging(kind) {
  const total = resourceState.pagination.counts[kind];
  document.querySelector(`[data-resource-count="${kind}"]`).textContent =
    `Showing ${resourceState[kind].length} of ${total} ${kind === 'artifacts' ? 'results' : kind}`;
  document.querySelector(`[data-load-more="${kind}"]`).hidden = offsets[kind] >= total;
}

function renderDatasets(current) {
  dataset.replaceChildren();
  for (const item of resourceState.datasets) {
    const option = element('option', item.relative_path.split('/').pop());
    option.value = String(item.id);
    dataset.append(option);
  }
  if (resourceState.datasets.some(item => String(item.id) === current)) dataset.value = current;
  dataset.disabled = !resourceState.datasets.length;
  document.querySelectorAll('[data-needs-dataset]').forEach(button => { button.disabled = dataset.disabled; });
  document.dispatchEvent(new Event('datasets-refreshed'));
}

function renderSchemas(current) {
  const group = schema.querySelector('optgroup:last-child');
  group.replaceChildren();
  for (const item of resourceState.schemas) {
    const option = element('option', item.label || `${item.name} · ${item.version} · #${item.id}`);
    option.value = `schema:${item.id}`; group.append(option);
  }
  schema.value = current;
}

function renderArtifacts() {
  const artifacts = document.querySelector('#artifacts');
  artifacts.replaceChildren();
  for (const item of resourceState.artifacts) {
    const row = element('p');
    const link = element('a', item.relative_path.split('/').pop());
    link.href = `/api/projects/${projectId}/artifacts/${item.id}`;
    link.target = '_blank'; link.rel = 'noopener';
    const download = element('a', 'Download');
    download.href = `${link.href}?download=true`;
    row.append(link, element('span', ` · ${item.kind} · `, 'hint'), download);
    artifacts.append(row);
  }
  if (!resourceState.artifacts.length) artifacts.append(element('p', 'Converted data and reports will appear here.', 'hint'));
}

function renderJob(job, row) {
  row.dataset.jobStatus = job.status;
  row.replaceChildren(element('span', `${job.operation} · ${job.status} · ${job.operation_log?.at(-1) || ''} · ${job.output_filename || ''} `));
  const detailsOnly = terminal(job.status) || job.active === false;
  const button = element('button', detailsOnly ? 'View details' : 'Cancel', 'secondary');
  button.type = 'button';
  button.addEventListener('click', async () => {
    button.disabled = true;
    try {
      if (detailsOnly) {
        const detail = await request(`/api/jobs/${job.id}`);
        showResult(`Job ${detail.status}`, detail.result || detail);
        showSliceResult(detail.result, resourceState);
      } else {
        await request(`/api/jobs/${job.id}/cancel`, new FormData());
      }
    } catch (error) { showResult('Job request failed', error.payload || {error: {message: error.message}}); }
    finally { button.disabled = false; }
  });
  row.append(button);
}

function renderJobs() {
  const jobs = document.querySelector('#jobs');
  const previous = new Map([...jobs.querySelectorAll('[data-job-id]')].map(row => [row.dataset.jobId, row]));
  const visible = uniqueRecords([...resourceState.jobs, ...(resourceState.active_jobs || [])]);
  const rows = visible.map(job => {
    const row = previous.get(job.id) || element('p'); row.dataset.jobId = job.id;
    renderJob(job, row); previous.delete(job.id); return row;
  });
  // An older running job stays visible until its current operation finishes.
  for (const [id, row] of previous) if (watching.has(id)) rows.push(row);
  jobs.replaceChildren(...rows);
  if (!rows.length) jobs.append(element('p', 'No jobs yet.', 'hint'));
  for (const job of visible) if (!terminal(job.status) && job.active !== false) void followJob(job.id);
}

function applyPage(project, kind, append = false, selectedDataset) {
  if (kind === 'jobs') resourceState.active_jobs = project.active_jobs || [];
  const current = kind === 'datasets' ? String(selectedDataset ?? dataset.value) : schema.value;
  const prior = resourceState[kind];
  let items = append ? [...prior, ...project[kind]] : [...project[kind]];
  if (kind === 'datasets' && !items.some(item => String(item.id) === current)) {
    const selected = prior.find(item => String(item.id) === current);
    if (selected) items.push(selected);
  }
  if (kind === 'schemas' && current.startsWith('schema:') && !items.some(item => `schema:${item.id}` === current)) {
    const selected = prior.find(item => `schema:${item.id}` === current);
    const option = schema.selectedOptions[0];
    if (selected) items.push(selected);
    else if (option) items.push({id: current.slice(7), label: option.textContent});
  }
  resourceState[kind] = uniqueRecords(items);
  resourceState.pagination.counts[kind] = project.pagination.counts[kind];
  offsets[kind] = project.pagination.offset + project[kind].length;
  if (kind === 'datasets') renderDatasets(current);
  else if (kind === 'schemas') renderSchemas(current);
  else if (kind === 'artifacts') renderArtifacts();
  else renderJobs();
  updatePaging(kind);
}

async function refreshResources(selectedDataset) {
  const epoch = ++refreshEpoch;
  for (const kind of resourceKinds) pageEpochs[kind]++;
  const project = await request(pageUrl());
  if (epoch !== refreshEpoch) return resourceState;
  for (const kind of resourceKinds) {
    pageEpochs[kind]++;
    applyPage(project, kind, false, selectedDataset);
  }
  return resourceState;
}

function queueResourceRefresh() {
  if (!pendingRefresh) {
    pendingRefresh = new Promise(resolve => setTimeout(resolve, 100))
      .then(() => refreshResources()).finally(() => { pendingRefresh = undefined; });
  }
  return pendingRefresh;
}

async function followJob(id) {
  id = String(id);
  if (watching.has(id)) return;
  watching.add(id);
  const jobs = document.querySelector('#jobs');
  document.querySelector('#no-jobs')?.remove();
  let row = jobs.querySelector(`[data-job-id="${CSS.escape(id)}"]`);
  if (!row) { row = element('p'); row.dataset.jobId = id; jobs.prepend(row); }
  try {
    while (true) {
      const job = await request(`/api/jobs/${id}`);
      renderJob(job, row);
      if (terminal(job.status)) {
        const project = await queueResourceRefresh();
        // A coalesced refresh may have captured an older active snapshot. The
        // terminal detail we already observed must win in both state and DOM.
        const {result, ...finished} = job;
        finished.active = false;
        finished.operation_log = job.operation_log?.slice(-1) || [];
        resourceState.active_jobs = (resourceState.active_jobs || []).filter(item => String(item.id) !== id);
        resourceState.jobs = resourceState.jobs.map(item => String(item.id) === id ? finished : item);
        renderJob(finished, row);
        showSliceResult(result, project);
        if (result) showResult(`Job ${job.status}`, result);
        break;
      }
      await new Promise(resolve => setTimeout(resolve, 500));
    }
  } catch (error) { row.append(element('span', error.message, 'error')); }
  finally { watching.delete(id); }
}

document.querySelector('form[action="/api/projects"]')?.addEventListener('submit', async event => {
  event.preventDefault();
  const form = event.currentTarget; const button = form.querySelector('button'); button.disabled = true;
  try {
    const project = await request(form.action, new FormData(form));
    window.location.assign(`/projects/${project.id}`);
  } catch (error) {
    document.querySelector('#home-error').textContent = error.message;
    button.disabled = false;
  }
});

document.querySelectorAll('form[data-operation]').forEach(form => {
  form.addEventListener('submit', async event => {
    event.preventDefault();
    const button = form.querySelector('button'); button.disabled = true;
    const operation = form.dataset.operation; const data = new FormData(form);
    data.set('schema', schema.value); data.set('dataset_id', dataset.value);
    if (operation === 'convert') data.set('mapping_json', document.querySelector('#mapping-json').value);
    if (operation === 'zarr') {
      data.delete('files');
      for (const file of form.querySelector('input[type="file"]').files) data.append('files', file, file.webkitRelativePath);
    }
    showResult('Working…', {message: 'Processing your request.'});
    try {
      const result = await request(form.action, data);
      if (operation === 'schema') {
        const option = element('option', `${result.name} · ${result.version} · #${result.id}`);
        option.value = result.selector; schema.querySelector('optgroup:last-child').append(option);
        schema.value = result.selector; showResult('Schema added', result);
      } else if (result.job_id) {
        showResult('Job queued', {message: 'Progress is shown below.'}); void followJob(result.job_id);
      } else {
        if (result.dataset_id) await refreshResources(result.dataset_id);
        showResult(operation === 'validate' ? 'Validation result' : 'Upload complete', result);
      }
    } catch (error) { showResult('Operation failed', error.payload || {error: {message: error.message}}); }
    finally { button.disabled = false; }
  });
});

document.querySelector('#output-format')?.addEventListener('change', event => {
  const suffix = {hdf5: '.h5', netcdf: '.nc', zarr: '.zarr', parquet: '.parquet'}[event.target.value];
  const output = document.querySelector('#convert-output'); output.value = output.value.replace(/\.[^/.]+$/, '') + suffix;
});
document.querySelector('#report-format')?.addEventListener('change', event => {
  const suffix = {html: '.html', markdown: '.md', json: '.json'}[event.target.value];
  const output = document.querySelector('#report-output'); output.value = output.value.replace(/\.[^/.]+$/, '') + suffix;
});
if (projectId && resourceState) {
  document.querySelectorAll('[data-load-more]').forEach(button => {
    button.addEventListener('click', async () => {
      button.disabled = true;
      const kind = button.dataset.loadMore;
      const epoch = pageEpochs[kind];
      try {
        const project = await request(pageUrl(offsets[kind]));
        if (epoch === pageEpochs[kind]) applyPage(project, kind, true);
      }
      catch (error) { showResult('Could not load older records', error.payload || {error: {message: error.message}}); }
      finally { button.disabled = false; }
    });
  });
  for (const kind of resourceKinds) updatePaging(kind);
  renderJobs();
}
setupFields({request, showResult, followJob});
setupAuthoring({request, showResult});
