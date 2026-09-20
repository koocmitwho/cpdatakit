export function setupAuthoring({request, showResult, selectedContext}) {
  const draftButton = document.querySelector('#draft-schema');
  if (!draftButton) return;
  const project = document.body.dataset.projectId;
  const dataset = document.querySelector('#dataset');
  const schema = document.querySelector('#schema');
  const editor = document.querySelector('#schema-draft-json');
  const mapping = document.querySelector('#mapping-json');
  const status = document.querySelector('#authoring-status');
  draftButton.addEventListener('click', async () => {
    draftButton.disabled = true;
    try {
      const result = await request(`/api/projects/${project}/datasets/${dataset.value}/schema-draft`);
      const value = result.value;
      editor.value = JSON.stringify(value.schema, null, 2);
      const review = document.querySelector('#schema-review'); review.replaceChildren();
      const properties = {unit: '单位', role: '数据用途', dtype: '数据类型', conventions: '物理约定'};
      const questions = {
        unit: '请对照来源说明确认单位，未知时不要猜测。',
        role: '请确认这是测量值、模拟值还是其他用途的数据。',
        dtype: '请检查原始值并选择支持的数据类型。',
        conventions: '请确认物理定义、坐标系及适用的张量约定。',
      };
      for (const item of value.review) {
        const line = document.createElement('li');
        line.textContent = `${item.field || '整个数据集'} · ${properties[item.property] || item.property}：${item.observed ?? '未声明'}。${questions[item.property] || item.question}`;
        review.append(line);
      }
      const fields = value.schema.fields || [...value.schema.coordinates, ...value.schema.variables];
      const draft = {mappings: fields.map(field => ({source: field.name, target: field.name}))};
      if (value.schema.schema_version === '2.0') { draft.mapping_version = '2.0'; draft.dimensions = {}; }
      mapping.value = JSON.stringify(draft, null, 2);
      document.querySelector('#draft-editor').hidden = false;
      status.textContent = '草稿已生成。请核对下列声明并补全缺失信息，再保存数据规则。';
    } catch (error) { status.textContent = error.message; }
    finally { draftButton.disabled = false; }
  });
  document.querySelector('#save-draft').addEventListener('click', async event => {
    const button = event.currentTarget; button.disabled = true;
    try {
      const payload = JSON.parse(editor.value);
      const data = new FormData(); data.set('file', new Blob([JSON.stringify(payload)], {type: 'application/json'}), 'reviewed-schema.json');
      const result = await request(`/api/projects/${project}/schemas`, data);
      const option = document.createElement('option'); option.value = result.selector;
      option.textContent = `${result.name} · ${result.version} · #${result.id}`;
      schema.querySelector('optgroup:last-child').append(option); schema.value = result.selector;
      schema.dispatchEvent(new Event('change'));
      status.textContent = '数据规则已保存并选中，请重新校验当前数据。';
    } catch (error) { status.textContent = error.message; }
    finally { button.disabled = false; }
  });
  document.querySelector('#preview-mapping').addEventListener('click', async event => {
    const button = event.currentTarget; button.disabled = true;
    const context = selectedContext?.();
    try {
      const data = new FormData(); data.set('dataset_id', dataset.value); data.set('schema', schema.value);
      data.set('mapping_json', mapping.value || '{"mappings": []}');
      const result = await request(`/api/projects/${project}/mapping-preview`, data);
      showResult('映射预览', result, context);
      const table = document.querySelector('#mapping-preview tbody'); table.replaceChildren();
      for (const field of result.value.fields) {
        const row = document.createElement('tr');
        for (const text of [field.source, field.target || '（移除）', `${field.source_unit || '未声明'} → ${field.target_unit || '未声明'}`,
                            `${field.source_dims.join(', ')} → ${field.target_dims?.join(', ') || '移除'}`,
                            `${JSON.stringify(field.before)} → ${JSON.stringify(field.after)}`]) {
          const cell = document.createElement('td'); cell.textContent = text; row.append(cell);
        }
        table.append(row);
      }
      document.querySelector('#mapping-preview').hidden = false;
    } catch (error) { showResult('映射预览失败', error.payload || {error: {message: error.message}}, context); }
    finally { button.disabled = false; }
  });
}
