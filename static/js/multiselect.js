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

// Cuantas filas se dibujan de una vez. Con listas cortas (sucursal,
// marca: decenas) no cambia nada; con la de clientes (7.445) si:
// dibujar una fila por opcion tardaba 425 ms cada vez que se abria el
// panel. El resto se encuentra escribiendo en el buscador, que es como
// se usa una lista de ese tamano de todas formas.
const MSEL_TOPE_FILAS = 300;

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

    const coinciden = opts.filter(o => o.textContent.toLowerCase().includes(filtro));

    // Las tildadas van primero: si no, con el tope de arriba una
    // seleccion que cae mas abajo del corte no se ve al reabrir el
    // panel. El sort es estable, asi que dentro de cada grupo se
    // conserva el orden alfabetico con que vino la lista.
    const visibles = coinciden.length > MSEL_TOPE_FILAS
      ? [...coinciden].sort((a, b) =>
          (pendiente.has(b.value) ? 1 : 0) - (pendiente.has(a.value) ? 1 : 0))
      : coinciden;

    visibles.slice(0, MSEL_TOPE_FILAS).forEach(o => {
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

    // Que el corte se vea: sin esto la lista parece terminar ahi.
    if (coinciden.length > MSEL_TOPE_FILAS) {
      const aviso = document.createElement('div');
      aviso.className = 'msel-mas';
      aviso.textContent = 'y ' + (coinciden.length - MSEL_TOPE_FILAS)
                        + ' mas — escribe arriba para encontrarlos';
      listEl.appendChild(aviso);
    }
  }

  // El panel cuelga del boton hacia abajo. En los filtros del fondo del
  // panel lateral eso dejaba Cancelar/Aplicar fuera de la ventana, sin
  // forma de llegar a ellos: medido en Vta Acumulada, el boton de
  // Cliente terminaba en y=610 de 768 y Aplicar caia en y=951.
  // Se elige el lado con mas aire y se recorta la lista a lo que quepa,
  // asi los botones quedan siempre a la vista.
  function acomodar() {
    const MARGEN = 12;                       // aire contra el borde
    const MINIMO = 120;                      // no dejar la lista inservible

    panel.classList.remove('msel-arriba');
    listEl.style.maxHeight = '';
    panel.style.transform = '';

    const r = btn.getBoundingClientRect();
    const abajo  = window.innerHeight - r.bottom - MARGEN;
    const arriba = r.top - MARGEN;
    // Alto del panel sin la lista: buscador + botones. Se mide con el
    // panel ya visible, por eso acomodar() corre despues de mostrarlo.
    const fijo = panel.offsetHeight - listEl.offsetHeight;

    const haciaArriba = panel.offsetHeight > abajo && arriba > abajo;
    if (haciaArriba) panel.classList.add('msel-arriba');

    // Solo se ACHICA. El alto normal lo sigue poniendo el CSS
    // (max-height: 240px): si se dejara crecer hasta el espacio libre,
    // el desplegable se volveria mas alto que antes en todas las
    // pantallas, y eso no es lo que hay que arreglar aca.
    const disponible = (haciaArriba ? arriba : abajo) - fijo;
    if (disponible < listEl.offsetHeight) {
      listEl.style.maxHeight = Math.max(disponible, MINIMO) + 'px';
    }

    // Ultimo recurso: con la ventana muy baja no cabe ni con la lista
    // en el minimo (probado a 420 px de alto: sobresalia 21 px y los
    // botones volvian a quedar fuera). Se despega el panel del boton
    // lo justo para que entre entero -- feo, pero preferible a dejar
    // Aplicar fuera de la pantalla.
    const rp = panel.getBoundingClientRect();
    let corrimiento = 0;
    if (rp.bottom > window.innerHeight - MARGEN) {
      corrimiento = (window.innerHeight - MARGEN) - rp.bottom;
    }
    if (rp.top + corrimiento < MARGEN) corrimiento = MARGEN - rp.top;
    if (corrimiento) panel.style.transform = 'translateY(' + Math.round(corrimiento) + 'px)';
  }

  function abrir() {
    const actual = seleccionActual();
    pendiente = actual.size === 0 ? new Set(opciones().map(o => o.value)) : actual;
    search.value = '';
    renderLista('');
    panel.hidden = false;
    acomodar();
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
  window.addEventListener('resize', () => { if (!panel.hidden) acomodar(); });

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
