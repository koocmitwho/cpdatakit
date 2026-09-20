let fieldStructure;
let loadedDataset;

function options(select, names, selected) {
  select.replaceChildren(...names.map(name => {
    const option = document.createElement('option'); option.value = name; option.textContent = name;
    return option;
  }));
  select.value = selected;
}

export function showSliceResult(result, project) {
  if (result?.operation !== 'plot_scientific_slice' || result.status !== 'succeeded') return;
  const artifactId = result.provenance?.artifact_id ??
    project.artifacts.find(item => item.relative_path === result.artifact)?.id;
  if (!artifactId) return;
  const url = `/api/projects/${project.project.id}/artifacts/${artifactId}`;
  const value = result.value;
  const positions = Object.entries(value.slice).map(([name, p]) => `${name}[${p.index}] = ${p.value ?? '无坐标值'} ${p.unit || ''}`).join('; ');
  document.querySelector('#slice-image').src = url;
  document.querySelector('#slice-image').alt = `${value.variable} 热图，${positions}`;
  document.querySelector('#slice-caption').textContent = `${value.variable} [${value.unit || '单位未声明'}] · ${positions} · ${value.shape.join(' × ')} · ${value.cmap}`;
  document.querySelector('#slice-download').href = `${url}?download=true`;
  document.querySelector('#slice-preview').hidden = false;
}

export function setupFields({request, showResult, followJob}) {
  const load = document.querySelector('#load-field');
  if (!load) return;
  const dataset = document.querySelector('#dataset');
  const form = document.querySelector('#slice-form');
  const variable = document.querySelector('#field-variable');
  const x = document.querySelector('#field-x');
  const y = document.querySelector('#field-y');
  const indices = document.querySelector('#slice-indices');
  const status = document.querySelector('#field-status');
  const project = document.body.dataset.projectId;
  function fixedAxes() {
    const field = fieldStructure.fields.find(item => item.name === variable.value);
    const previous = Object.fromEntries([...indices.querySelectorAll('input')].map(input => [input.dataset.dimension, input.value]));
    indices.replaceChildren();
    for (const name of field.dims.filter(name => name !== x.value && name !== y.value)) {
      const wrapper = document.createElement('div');
      const label = document.createElement('label');
      const input = document.createElement('input');
      input.id = `index-${indices.children.length}`; label.htmlFor = input.id;
      label.textContent = `${name} 索引（0–${fieldStructure.dimensions[name] - 1}）`;
      input.type = 'number'; input.min = '0'; input.max = fieldStructure.dimensions[name] - 1;
      input.step = '1'; input.required = true; input.value = previous[name] || '0'; input.dataset.dimension = name;
      wrapper.append(label, input); indices.append(wrapper);
    }
    status.textContent = `${field.name} [${field.unit || '单位未声明'}] · ${field.dims.map(name => `${name}=${fieldStructure.dimensions[name]}`).join(', ')}`;
  }
  function chooseVariable() {
    const dims = fieldStructure.fields.find(item => item.name === variable.value).dims;
    options(x, dims, dims.at(-1)); options(y, dims, dims.at(-2)); fixedAxes();
  }
  load.addEventListener('click', async () => {
    load.disabled = true;
    form.hidden = true;
    try {
      const result = await request(`/api/projects/${project}/datasets/${dataset.value}/structure`);
      fieldStructure = result.value; loadedDataset = dataset.value;
      const fields = fieldStructure.fields.filter(item => item.kind === 'variable' && item.dims.length >= 2);
      if (!fields.length) { status.textContent = '当前数据没有二维或更高维度的变量。'; return; }
      options(variable, fields.map(item => item.name), fields.find(item => item.name === 'temperature')?.name || fields[0].name);
      chooseVariable(); form.hidden = false;
    } catch (error) { status.textContent = error.message; }
    finally { load.disabled = false; }
  });
  variable.addEventListener('change', chooseVariable);
  x.addEventListener('change', fixedAxes); y.addEventListener('change', fixedAxes);
  function changed() {
    if (loadedDataset !== dataset.value) {
      form.hidden = true; document.querySelector('#slice-preview').hidden = true;
      status.textContent = '数据已切换，请重新读取字段与维度。';
    }
  }
  dataset.addEventListener('change', changed);
  document.addEventListener('datasets-refreshed', changed);
  form.addEventListener('submit', async event => {
    event.preventDefault();
    const button = form.querySelector('button'); button.disabled = true;
    try {
      const payload = new FormData(form); payload.set('dataset_id', loadedDataset);
      payload.set('indices', JSON.stringify(Object.fromEntries([...indices.querySelectorAll('input')].map(input => [input.dataset.dimension, Number(input.value)]))));
      const result = await request(`/api/projects/${project}/slice`, payload);
      void followJob(result.job_id);
    } catch (error) { showResult('热图生成失败', error.payload || {error: {message: error.message}}); }
    finally { button.disabled = false; }
  });
}
