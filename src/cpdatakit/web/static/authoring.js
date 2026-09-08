export function setupAuthoring({request, showResult}) {
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
      for (const item of value.review) {
        const line = document.createElement('li');
        line.textContent = `${item.field || 'Dataset'} · ${item.property}: ${item.observed ?? 'not declared'}. ${item.question}`;
        review.append(line);
      }
      const fields = value.schema.fields || [...value.schema.coordinates, ...value.schema.variables];
      const draft = {mappings: fields.map(field => ({source: field.name, target: field.name}))};
      if (value.schema.schema_version === '2.0') { draft.mapping_version = '2.0'; draft.dimensions = {}; }
      mapping.value = JSON.stringify(draft, null, 2);
      document.querySelector('#draft-editor').hidden = false;
      status.textContent = 'Draft ready. Check the listed declarations and fill in the missing values.';
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
      status.textContent = 'Schema saved and selected.';
    } catch (error) { status.textContent = error.message; }
    finally { button.disabled = false; }
  });
  document.querySelector('#preview-mapping').addEventListener('click', async event => {
    const button = event.currentTarget; button.disabled = true;
    try {
      const data = new FormData(); data.set('dataset_id', dataset.value); data.set('schema', schema.value);
      data.set('mapping_json', mapping.value || '{"mappings": []}');
      const result = await request(`/api/projects/${project}/mapping-preview`, data);
      showResult('Mapping preview', result);
      const table = document.querySelector('#mapping-preview tbody'); table.replaceChildren();
      for (const field of result.value.fields) {
        const row = document.createElement('tr');
        for (const text of [field.source, field.target || '(dropped)', `${field.source_unit || 'unknown'} → ${field.target_unit || 'unknown'}`,
                            `${field.source_dims.join(', ')} → ${field.target_dims?.join(', ') || 'dropped'}`,
                            `${JSON.stringify(field.before)} → ${JSON.stringify(field.after)}`]) {
          const cell = document.createElement('td'); cell.textContent = text; row.append(cell);
        }
        table.append(row);
      }
      document.querySelector('#mapping-preview').hidden = false;
    } catch (error) { showResult('Mapping preview failed', error.payload || {error: {message: error.message}}); }
    finally { button.disabled = false; }
  });
}
