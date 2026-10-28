export default function({data, parentElement, setTriggerValue}) {
  const root = parentElement.querySelector('.sort-root');
  root.replaceChildren();
  const original = data.rows.map(row => row.id);
  let dragged = null;
  let sent = false;
  const groups = new Map();
  const status = document.createElement('div');
  status.className = 'status';
  status.setAttribute('role', 'status');
  status.textContent = '拖动左侧手柄排序，松手自动保存；也可聚焦手柄后按 ↑ / ↓。';
  const send = () => {
    const ordered = Array.from(root.querySelectorAll('.item'), el => Number(el.dataset.id));
    if (sent || ordered.every((id, index) => id === original[index])) return;
    sent = true;
    status.textContent = '正在保存…';
    setTriggerValue('changed', {original_ids: original, ordered_ids: ordered,
      revision: data.revision, event_id: Date.now().toString(36) + '-' + Math.random().toString(36).slice(2)});
  };
  const clearHighlights = () => root.querySelectorAll('.over').forEach(el => el.classList.remove('over'));
  for (const row of data.rows) {
    if (!groups.has(row.group)) {
      const section = document.createElement('div');
      section.className = 'group';
      const title = document.createElement('div');
      title.className = 'group-title';
      title.textContent = row.group;
      section.appendChild(title);
      root.appendChild(section);
      groups.set(row.group, section);
    }
    const section = groups.get(row.group);
    const item = document.createElement('div');
    item.className = 'item';
    item.dataset.id = row.id;
    const handle = document.createElement('button');
    handle.className = 'handle';
    handle.textContent = '⠿';
    handle.draggable = true;
    handle.type = 'button';
    handle.setAttribute('aria-label', '拖动排序 ' + row.label);
    const label = document.createElement('span');
    label.textContent = row.label;  // Stock names are data, never HTML.
    item.append(handle, label);
    section.appendChild(item);
    handle.ondragstart = event => {
      if (sent) { event.preventDefault(); return; }
      dragged = item;
      event.dataTransfer.effectAllowed = 'move';
      event.dataTransfer.setData('text/plain', String(row.id));
      item.classList.add('dragging');
    };
    handle.ondragend = () => {
      item.classList.remove('dragging');
      dragged = null;
      clearHighlights();
    };
    item.ondragover = event => {
      if (sent || !dragged || dragged === item || dragged.parentElement !== section) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = 'move';
      clearHighlights();
      item.classList.add('over');
    };
    item.ondrop = event => {
      event.preventDefault();
      clearHighlights();
      if (sent || !dragged || dragged === item || dragged.parentElement !== section) return;
      const items = Array.from(section.querySelectorAll('.item'));
      const movingDown = items.indexOf(dragged) < items.indexOf(item);
      section.insertBefore(dragged, movingDown ? item.nextSibling : item);
      send();
    };
    handle.onkeydown = event => {
      if (sent || !['ArrowUp', 'ArrowDown'].includes(event.key)) return;
      event.preventDefault();
      const neighbor = event.key === 'ArrowUp' ? item.previousElementSibling : item.nextElementSibling;
      if (!neighbor || !neighbor.classList.contains('item')) return;
      section.insertBefore(item, event.key === 'ArrowUp' ? neighbor : neighbor.nextSibling);
      handle.focus();
      send();
    };
  }
  root.appendChild(status);
  return () => root.replaceChildren();
}
