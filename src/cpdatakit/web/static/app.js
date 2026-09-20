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
const jobContexts = new Map();
const terminal = status => ['succeeded', 'failed', 'cancelled'].includes(status);
const statusLabels = {queued: '等待处理', running: '处理中', succeeded: '已完成', failed: '失败', cancelled: '已取消'};
const operationLabels = {convert: '转换数据', convert_and_write: '转换数据', report: '生成报告', build_report: '生成报告', plot: '绘图', slice: '绘制切片', plot_scientific_slice: '绘制切片', compare: '比较报告'};
let pendingRefresh;
let refreshEpoch = 0;
let validationSnapshot;
let resultNotice;

async function request(url, data) {
  const response = await fetch(url, data ? {
    method: 'POST', headers: {'X-CSRF-Token': csrf}, body: data,
  } : {});
  const result = await response.json();
  if (!response.ok) {
    const error = new Error(result.error?.message || result.detail || '请求失败，请稍后重试。');
    error.payload = result;
    error.status = response.status;
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

function selectedContext() {
  return {
    datasetId: dataset?.value, schemaSelector: schema?.value,
    filename: dataset?.selectedOptions[0]?.textContent || '',
    schemaLabel: schema?.selectedOptions[0]?.textContent || schema?.value || '',
  };
}

function updateWorkflow() {
  const current = selectedContext();
  const fileLabel = document.querySelector('#current-dataset');
  const ruleLabel = document.querySelector('#current-schema');
  if (fileLabel) fileLabel.textContent = current.filename || '尚未选择数据';
  if (ruleLabel) ruleLabel.textContent = current.schemaLabel || '尚未选择数据规则';
  const status = document.querySelector('#workflow-status');
  let message = '请选择数据并运行校验';
  if (validationSnapshot) {
    const previous = validationSnapshot.context;
    const matches = previous && previous.datasetId === current.datasetId && previous.schemaSelector === current.schemaSelector;
    const subject = validationSnapshot.mapped ? '映射后数据' : '当前数据';
    message = matches
      ? (validationSnapshot.valid ? `${subject}校验通过；仍需结合领域知识判断科学含义。` : `${subject}校验未通过，请检查下方错误。`)
      : '历史校验结果：当前数据或规则已切换，或该任务未记录选择；请对当前选择重新校验。';
    if (resultNotice) {
      resultNotice.textContent = matches ? '此结果对应当前选择。' : '历史结果，仅对应下方记录的文件与规则。';
      resultNotice.className = matches ? 'hint' : 'status-badge is-warning';
    }
  }
  if (status) status.textContent = message;
}

function artifactActions(payload) {
  const id = payload.provenance?.artifact_id;
  if (payload.status !== 'succeeded' || !/^\d+$/.test(String(id)) || Number(id) <= 0) return null;
  const actions = element('div', undefined, 'result-actions');
  const link = element('a', '查看结果');
  link.href = `/api/projects/${projectId}/artifacts/${id}`;
  link.target = '_blank'; link.rel = 'noopener';
  const download = element('a', '下载'); download.href = `${link.href}?download=true`;
  actions.append(link, download);
  return actions;
}

function showResult(title, payload, context) {
  document.querySelector('#operation-result').hidden = false;
  document.querySelector('#result-title').textContent = title;
  const content = document.querySelector('#result-content');
  content.replaceChildren();
  const value = payload.value || payload;
  const report = value.report || value;
  const validation = value.validation || report.validation || value.schema?.validation;
  const summary = value.summary || report.statistics || value;
  const actualFile = payload.provenance?.input_filename || report.file?.filename || context?.filename;
  const actualSchema = report.schema?.profile || report.schema?.schema?.profile || context?.schemaLabel;
  validationSnapshot = validation ? {context, valid: validation.valid, mapped: payload.operation === 'preview_mapping' || payload.operation === 'convert_and_write'} : null;
  resultNotice = null;
  if (actualFile) content.append(element('p', `本次文件：${actualFile}`));
  if (actualSchema) content.append(element('p', `本次规则：${actualSchema}`));
  else if (validation) content.append(element('p', '本次结果未记录规则名称，请查看详细记录。', 'hint'));
  if (payload.error) {
    content.append(element('p', typeof payload.error === 'string' ? payload.error : payload.error.message, 'error'));
    if (payload.error.action) content.append(element('p', payload.error.action));
  }
  if (value.message) content.append(element('p', value.message));
  if (validation) {
    const errors = validation.errors || [], warnings = validation.warnings || [];
    content.append(element('p', validation.valid ? '校验通过' : '校验未通过',
                           `status-badge ${validation.valid ? (warnings.length ? 'is-warning' : 'is-success') : 'is-error'}`));
    resultNotice = element('p'); content.append(resultNotice);
    const metrics = element('div', undefined, 'summary-grid');
    const recordCount = summary.record_count ?? report.record_count;
    const fieldCount = summary.field_count ?? (summary.fields ? Object.keys(summary.fields).length : undefined);
    for (const [label, count] of [['记录', recordCount], ['字段', fieldCount], ['错误', errors.length], ['警告', warnings.length]]) {
      if (count !== undefined) metrics.append(element('p', `${label} ${count}`, 'metric'));
    }
    content.append(metrics);
    for (const [label, issues] of [['错误', errors], ['警告', warnings]]) for (const issue of issues) {
      const affected = issue.affected_records !== undefined ? `；涉及数量 ${issue.affected_records}` : '';
      content.append(element('p', `${label} · ${issue.field || '整个数据集'}：${issue.message}${affected}`));
      if (issue.suggestion) content.append(element('p', `处理建议：${issue.suggestion}`, 'hint'));
    }
    content.append(element('p', '校验检查已声明的字段、维度和单位；通过校验不代表物理或科学结论已经验证。', 'hint'));
    if (payload.operation === 'preview_mapping') content.append(element('p', '此处校验的是映射后的预览值，尚未写出或保存转换文件。', 'hint'));
  }
  if (value.file?.format) content.append(element('p', `数据格式：${value.file.format}`));
  const dimensions = summary.dimensions || report.dimensions;
  if (dimensions && Object.keys(dimensions).length) {
    content.append(element('p', `维度：${Object.entries(dimensions).map(([k, v]) => `${k} = ${v}`).join(', ')}`));
  }
  if (!validation && value.record_count !== undefined) content.append(element('p', `记录 ${value.record_count}`));
  if (payload.artifact) content.append(element('p', `结果文件：${payload.artifact.split('/').pop()}`));
  const actions = artifactActions(payload); if (actions) content.append(actions);
  const details = element('details');
  details.append(element('summary', '查看详细记录（JSON）'), element('pre', JSON.stringify(payload, null, 2)));
  content.append(details);
  updateWorkflow();
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
    `已显示 ${resourceState[kind].length} 项，共 ${total} 项${{datasets: '数据', artifacts: '结果', schemas: '规则', jobs: '任务'}[kind]}`;
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
  updateWorkflow();
}

function renderSchemas(current) {
  const group = schema.querySelector('optgroup:last-child');
  group.replaceChildren();
  for (const item of resourceState.schemas) {
    const option = element('option', item.label || `${item.name} · ${item.version} · #${item.id}`);
    option.value = `schema:${item.id}`; group.append(option);
  }
  schema.value = current;
  updateWorkflow();
}

function renderArtifacts() {
  const artifacts = document.querySelector('#artifacts');
  artifacts.replaceChildren();
  for (const item of resourceState.artifacts) {
    const row = element('div', undefined, 'artifact-row');
    const link = element('a', item.relative_path.split('/').pop());
    link.href = `/api/projects/${projectId}/artifacts/${item.id}`;
    link.target = '_blank'; link.rel = 'noopener';
    const download = element('a', '下载');
    download.href = `${link.href}?download=true`;
    row.append(link, element('span', operationLabels[item.kind] || item.kind, 'hint'), download);
    artifacts.append(row);
  }
  if (!resourceState.artifacts.length) artifacts.append(element('p', '完成转换或生成报告后，可在这里查看和下载结果。', 'hint'));
}

function renderJob(job, row) {
  row.dataset.jobStatus = job.status;
  row.className = 'job-row';
  const label = operationLabels[job.operation] || job.operation || '任务';
  const state = element('span', statusLabels[job.status] || job.status, `status-badge ${job.status === 'succeeded' ? 'is-success' : job.status === 'failed' ? 'is-error' : 'is-warning'}`);
  row.replaceChildren(element('span', label), state);
  if (job.output_filename || job.input_filename) row.append(element('span', job.output_filename || job.input_filename));
  const step = job.operation_log?.at(-1);
  if (step && !terminal(job.status) && !['running', 'queued', job.operation].includes(step)) row.append(element('span', `进度：${step}`, 'hint'));
  if (job.persistence?.state === 'pending') {
    const pending = element('span', '结果等待保存，请保留此任务记录。', 'hint');
    pending.dataset.persistence = 'pending'; row.append(pending);
  }
  const detailsOnly = terminal(job.status) || job.active === false;
  const button = element('button', detailsOnly ? '查看详情' : '取消任务', 'secondary');
  button.type = 'button';
  button.addEventListener('click', async () => {
    button.disabled = true;
    try {
      if (detailsOnly) {
        const detail = await request(`/api/jobs/${job.id}`);
        showResult(`任务${statusLabels[detail.status] || detail.status}`, detail.result || detail, jobContexts.get(String(job.id)));
        showSliceResult(detail.result, resourceState);
      } else {
        await request(`/api/jobs/${job.id}/cancel`, new FormData());
      }
    } catch (error) { showResult('读取任务失败', error.payload || {error: {message: error.message}}); }
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
  if (!rows.length) jobs.append(element('p', '尚无任务。转换、报告和绘图任务会显示在这里。', 'hint'));
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
        if (result) showResult(`任务${statusLabels[job.status] || job.status}`, result, jobContexts.get(id));
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
    const context = selectedContext();
    const feedback = document.querySelector(`#output-feedback-${operation}`);
    if (feedback) { feedback.textContent = ''; feedback.className = 'hint'; }
    data.set('schema', schema.value); data.set('dataset_id', dataset.value);
    if (operation === 'convert') data.set('mapping_json', document.querySelector('#mapping-json').value);
    if (operation === 'zarr') {
      data.delete('files');
      for (const file of form.querySelector('input[type="file"]').files) data.append('files', file, file.webkitRelativePath);
    }
    showResult('处理中…', {message: '正在处理请求，请稍候。'}, context);
    try {
      const result = await request(form.action, data);
      if (operation === 'schema') {
        const option = element('option', `${result.name} · ${result.version} · #${result.id}`);
        option.value = result.selector; schema.querySelector('optgroup:last-child').append(option);
        schema.value = result.selector; schema.dispatchEvent(new Event('change'));
        showResult('已添加数据规则', result);
      } else if (result.job_id) {
        jobContexts.set(String(result.job_id), context);
        if (feedback) feedback.textContent = `输出路径：${data.get('output')}。任务完成后可查看或下载结果。`;
        showResult('任务已排队', {message: '可在下方任务列表查看进度。'}, context); void followJob(result.job_id);
      } else {
        if (result.dataset_id) await refreshResources(result.dataset_id);
        showResult(operation === 'validate' ? '校验结果' : '上传完成', result, operation === 'validate' ? context : undefined);
      }
    } catch (error) {
      const conflict = error.status === 409 && ['overwrite_confirmation', 'output_exists'].includes(error.payload?.error?.code);
      if (conflict && ['convert', 'report'].includes(operation)) {
        const path = data.get('output');
        const message = `输出路径已存在：${path}。请更名，或明确勾选“我确认替换”以覆盖同名文件后重新提交。`;
        if (feedback) { feedback.textContent = message; feedback.className = 'error'; }
        document.querySelector(`#${operation}-output`)?.focus();
        showResult('输出文件重名', {error: {message}, details: error.payload}, context);
      } else showResult('操作失败', error.payload || {error: {message: error.message}}, context);
    }
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
      catch (error) { showResult('无法加载更早的记录', error.payload || {error: {message: error.message}}); }
      finally { button.disabled = false; }
    });
  });
  for (const kind of resourceKinds) updatePaging(kind);
  renderJobs();
}
dataset?.addEventListener('change', updateWorkflow);
schema?.addEventListener('change', updateWorkflow);
updateWorkflow();
setupFields({request, showResult, followJob});
setupAuthoring({request, showResult, selectedContext});
