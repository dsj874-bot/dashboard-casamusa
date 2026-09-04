// Selector multiple tipo Excel/Power BI para reemplazar visualmente un
// <select> simple, sin cambiar como el resto del código lee sus
// valores: el <select> original queda oculto pero sigue siendo la
// fuente de verdad (sus <option> se marcan .selected según lo que el
// usuario tilde). Usar mselValores()/mselLimpiar() para leer/resetear.
//
// Uso:
//   initMultiSelect(document.getElementById('f-sucursal'));
//   ...
//   const seleccion = mselValores(document.getElementById('f-sucursal')); // string[]
//   mselLimpiar(document.getElementById('f-sucursal')); // vuelve a "Todas"

function initMultiSelect(selectEl, opts) {
  if (!selectEl || selectEl._msel) return selectEl && selectEl._msel;
  opts = opts || {};
  const etiquetaTodo = opts.etiquetaTodo || 'Todas';

  selectEl.multiple = true;
  // Mientras el <select> se poblaba (antes de volverse multiple) el
  // navegador puede haber auto-seleccionado la primera opcion, como
  // hace cualquier <select> simple -- se limpia para arrancar siempre
  // en "nada seleccionado" = sin filtro / Todas.
  Array.from(selectEl.options).forEach(o => { o.selected = false; });
  selectEl.classList.add('msel-native');

  const wrap = document.createElement('div');
  wrap.className = 'msel';
  selectEl.parentNode.insertBefore(wrap, selectEl);
  wrap.appendChild(selectEl);

  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'msel-btn';
  wrap.appendChild(btn);

  const panel = document.createElement('div');
  panel.className = 'msel-panel';
  panel.hidden = true;
  wrap.appendChild(panel);

  const search = document.createElement('input');
  search.type = 'text';
  search.className = 'msel-search';
  search.placeholder = 'Buscar...';
  panel.appendChild(search);

  const listEl = document.createElement('div');
  listEl.className = 'msel-list';
  panel.appendChild(listEl);

  const actions = document.createElement('div');
  actions.className = 'msel-actions';
  const btnCancel = document.createElement('button');
  btnCancel.type = 'button';
  btnCancel.className = 'msel-cancel';
  btnCancel.textContent = 'Cancelar';
  const btnApply = document.createElement('button');
  btnApply.type = 'button';
  btnApply.className = 'msel-apply';
  btnApply.textContent = 'Aplicar';
  actions.appendChild(btnCancel);
  actions.appendChild(btnApply);
  panel.appendChild(actions);

  let pendiente = new Set();

  const opciones = () => Array.from(selectEl.options);
  const seleccionActual = () => new Set(opciones().filter(o => o.selected).map(o => o.value));

  function actualizarBoton() {
    const sel = seleccionActual();
    const total = opciones().length;
    if (sel.size === 0 || sel.size === total) {
      btn.textContent = etiquetaTodo;
    } else if (sel.size === 1) {
      btn.textContent = opciones().find(o => sel.has(o.value)).textContent;
    } else {
      btn.textContent = sel.size + ' seleccionadas';
    }
    btn.classList.toggle('msel-activo', sel.size > 0 && sel.size < total);
  }

  function renderLista(filtro) {
    filtro = (filtro || '').toLowerCase();
    listEl.innerHTML = '';
    const opts = opciones();

    const filaTodo = document.createElement('label');
    filaTodo.className = 'msel-opt msel-opt-todo';
    const chkTodo = document.createElement('input');
    chkTodo.type = 'checkbox';
    chkTodo.checked = pendiente.size === opts.length;
    chkTodo.indeterminate = pendiente.size > 0 && pendiente.size < opts.length;
    chkTodo.addEventListener('change', () => {
      pendiente = chkTodo.checked ? new Set(opts.map(o => o.value)) : new Set();
      renderLista(search.value);
    });
    filaTodo.appendChild(chkTodo);
    filaTodo.appendChild(document.createTextNode('(Todo)'));
    listEl.appendChild(filaTodo);

    opts
      .filter(o => o.textContent.toLowerCase().includes(filtro))
      .forEach(o => {
        const fila = document.createElement('label');
        fila.className = 'msel-opt';
        const chk = document.createElement('input');
        chk.type = 'checkbox';
        chk.checked = pendiente.has(o.value);
        chk.addEventListener('change', () => {
          if (chk.checked) pendiente.add(o.value); else pendiente.delete(o.value);
          renderLista(search.value);
        });
        fila.appendChild(chk);
        fila.appendChild(document.createTextNode(o.textContent));
        listEl.appendChild(fila);
      });
  }

  function abrir() {
    const actual = seleccionActual();
    pendiente = actual.size === 0 ? new Set(opciones().map(o => o.value)) : actual;
    search.value = '';
    renderLista('');
    panel.hidden = false;
    btn.classList.add('msel-abierto');
    search.focus();
  }

  function cerrar() {
    panel.hidden = true;
    btn.classList.remove('msel-abierto');
  }

  btn.addEventListener('click', () => (panel.hidden ? abrir() : cerrar()));
  search.addEventListener('input', () => renderLista(search.value));
  btnCancel.addEventListener('click', cerrar);
  btnApply.addEventListener('click', () => {
    const opts = opciones();
    const todoMarcado = pendiente.size === opts.length;
    // Todo marcado equivale a "sin filtro" -- dejamos el <select> sin
    // opciones .selected, igual que cuando el usuario nunca eligio nada.
    opts.forEach(o => { o.selected = !todoMarcado && pendiente.has(o.value); });
    actualizarBoton();
    cerrar();
    selectEl.dispatchEvent(new Event('change', { bubbles: true }));
  });
  document.addEventListener('click', e => { if (!wrap.contains(e.target)) cerrar(); });

  selectEl._msel = { refrescar: actualizarBoton };
  actualizarBoton();
  return selectEl._msel;
}

function mselValores(selectEl) {
  if (!selectEl) return [];
  return Array.from(selectEl.selectedOptions).map(o => o.value);
}

function mselLimpiar(selectEl) {
  if (!selectEl) return;
  Array.from(selectEl.options).forEach(o => { o.selected = false; });
  if (selectEl._msel) selectEl._msel.refrescar();
}
