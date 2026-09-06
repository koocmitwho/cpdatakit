const csrf = document.querySelector('[name="csrf_token"]')?.value;
const projectId = document.body.dataset.projectId;
const schema = document.querySelector('#schema');
const dataset = document.querySelector('#dataset');

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

async function refreshResources(selectedDataset) {
  const project = await request(`/api/projects/${projectId}`);
  const current = String(selectedDataset || dataset.value);
  dataset.replaceChildren();
  for (const item of project.datasets) {
    const option = element('option', item.relative_path.split('/').pop());
    option.value = item.id;
    dataset.append(option);
  }
  if (project.datasets.some(item => String(item.id) === current)) dataset.value = current;
  dataset.disabled = !project.datasets.length;
  document.querySelectorAll('[data-needs-dataset]').forEach(button => { button.disabled = !project.datasets.length; });
  const artifacts = document.querySelector('#artifacts');
  artifacts.replaceChildren();
  for (const item of project.artifacts) {
    const row = element('p');
    const link = element('a', item.relative_path.split('/').pop());
    link.href = `/api/projects/${projectId}/artifacts/${item.id}`;
    link.target = '_blank'; link.rel = 'noopener';
    const download = element('a', 'Download');
    download.href = `${link.href}?download=true`;
    row.append(link, element('span', ` · ${item.kind} · `, 'hint'), download);
    artifacts.append(row);
  }
  if (!project.artifacts.length) artifacts.append(element('p', 'Converted data and reports will appear here.', 'hint'));
}

const watching = new Set();
async function followJob(id) {
  if (watching.has(id)) return;
  watching.add(id);
  const jobs = document.querySelector('#jobs');
  document.querySelector('#no-jobs')?.remove();
  let row = jobs.querySelector(`[data-job-id="${CSS.escape(id)}"]`);
  if (!row) { row = element('p'); row.dataset.jobId = id; jobs.prepend(row); }
  try {
    while (true) {
      const job = await request(`/api/jobs/${id}`);
      row.replaceChildren(element('span', `${job.operation} · ${job.status} · ${job.output_filename || ''} `));
      if (['succeeded', 'failed', 'cancelled'].includes(job.status)) {
        const button = element('button', 'View details', 'secondary'); button.type = 'button';
        button.addEventListener('click', () => showResult(`Job ${job.status}`, job.result || job));
        row.append(button);
        await refreshResources();
        if (job.result) showResult(`Job ${job.status}`, job.result);
        break;
      }
      const cancel = element('button', 'Cancel', 'secondary'); cancel.type = 'button';
      cancel.addEventListener('click', async () => {
        cancel.disabled = true;
        try { await request(`/api/jobs/${id}/cancel`, new FormData()); }
        catch (error) { showResult('Cancellation failed', error.payload || {error: {message: error.message}}); }
      });
      row.append(cancel);
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
if (projectId) document.querySelectorAll('[data-job-id]').forEach(row => void followJob(row.dataset.jobId));
