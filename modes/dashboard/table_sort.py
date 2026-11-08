"""Click-to-sort for every data table on every dashboard page.

Clicking a column header sorts ascending, clicking again sorts
descending, and a third click restores the order the page rendered.
Sorting is client-side only and reads what the cell shows, so a column
masked by screen privacy (``••••``) sorts as equal values and keeps the
original order rather than leaking a ranking of hidden amounts.

Values understand the dashboard's formats: ₹/$/Rs amounts, Indian lakh
(L) and crore (Cr) suffixes, K/M/B, signed percentages, ISO dates and
times. Blank or dash cells sort last in both directions.

Rows that belong together stay together: a row with ``expand-row``,
``detail-row`` or ``data-sort-attach`` moves with the row above it,
and ``total-row``/``data-sort-fixed`` rows stay at the bottom. Opt a
table or a header cell out with ``data-no-sort``; key-value tables
(``kv``/``kvtable``) are never sortable. A chosen sort survives live
refreshes and section reloads for the rest of the browser session.
"""

from __future__ import annotations


# Pure parser, kept separate so tests can run it under Node.
SORT_KEY_JS = r"""
function dashSortKey(text) {
  var value = String(text == null ? '' : text).replace(/\s+/g, ' ').trim();
  if (/^(?:[-\u2013\u2014\u2026.]*|n\/?a|none|nil|loading\W*)$/i.test(value)) return null;
  if (/^\d{4}-\d{2}-\d{2}/.test(value)) return { text: value, date: true };
  var time = /^(\d{1,2}):(\d{2})(?::(\d{2}))?(?!\d)/.exec(value);
  if (time) return { num: (+time[1]) * 3600 + (+time[2]) * 60 + (+(time[3] || 0)), text: value };
  var match = /([+\-\u2212])?\s*(?:\u20b9|\$|\bRs\.?|\bINR\b|\bUSD\b)?\s*([+\-\u2212])?\s*(\d[\d,]*(?:\.\d+)?|\.\d+)(?:\s*(crores?|cr|lakhs?|l|bn|k|m|b|x))?(?![\w.])/i.exec(value);
  if (!match) return { text: value };
  var units = { crore: 1e7, crores: 1e7, cr: 1e7, lakh: 1e5, lakhs: 1e5, l: 1e5,
                bn: 1e9, b: 1e9, m: 1e6, k: 1e3, x: 1 };
  var number = parseFloat(match[3].replace(/,/g, ''));
  number *= units[(match[4] || '').toLowerCase()] || 1;
  var negative = /[-\u2212]/.test((match[1] || '') + (match[2] || ''));
  return { num: negative ? -number : number, text: value };
}
"""


_SCRIPT = r"""
<script>
(function () {
  if (window.dashboardTableSort) return;
  __SORT_KEY__
  var STORE = 'dashTableSort';
  var SKIP = 'table.kv, table.kvtable, table[data-no-sort], [data-no-sort] table';
  var FIXED = '.total-row, .totals, .subtotal, [data-sort-fixed]';
  var ATTACHED = '.expand-row, .detail-row, [data-sort-attach]';
  var collator = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' });
  var observer = null;

  function headerRow(table) {
    if (table.tHead && table.tHead.rows.length) return table.tHead.rows[table.tHead.rows.length - 1];
    var first = table.rows[0];
    if (!first || !first.cells.length) return null;
    return Array.prototype.every.call(first.cells, function (c) { return c.tagName === 'TH'; }) ? first : null;
  }

  function columnOf(row, cell) {
    var index = 0;
    for (var i = 0; i < row.cells.length; i++) {
      if (row.cells[i] === cell) return index;
      index += row.cells[i].colSpan || 1;
    }
    return -1;
  }

  function cellAt(row, col) {
    var index = 0;
    for (var i = 0; i < row.cells.length; i++) {
      var span = row.cells[i].colSpan || 1;
      if (col < index + span) return row.cells[i];
      index += span;
    }
    return null;
  }

  // Sortable units: a row plus any attached detail rows; fixed rows trail.
  function group(table, head) {
    var list = [], fixed = [], parent = null, mixed = false;
    Array.prototype.forEach.call(table.rows, function (row) {
      var section = row.parentNode.tagName;
      if (row === head || section === 'THEAD' || section === 'TFOOT') return;
      if (!(head.compareDocumentPosition(row) & Node.DOCUMENT_POSITION_FOLLOWING)) return;
      if (parent && row.parentNode !== parent) mixed = true;
      parent = parent || row.parentNode;
      if (row.matches(FIXED)) { fixed.push(row); return; }
      if (row.matches(ATTACHED) && list.length) { list[list.length - 1].rows.push(row); return; }
      if (row.cells.length === 1 && (row.cells[0].colSpan || 1) > 1) { fixed.push(row); return; }
      if (row._sortOrigin == null) {
        table._sortNext = (table._sortNext || 0) + 1;
        row._sortOrigin = table._sortNext;
      }
      list.push({ rows: [row], origin: row._sortOrigin });
    });
    return mixed ? null : { list: list, fixed: fixed, parent: parent };
  }

  function compare(type, dir) {
    return function (a, b) {
      var ka = a.key, kb = b.key;
      var na = !ka || (type === 'num' && ka.num == null);
      var nb = !kb || (type === 'num' && kb.num == null);
      if (na || nb) return na && nb ? a.origin - b.origin : (na ? 1 : -1);
      var result = type === 'num' ? ka.num - kb.num : collator.compare(ka.text, kb.text);
      if (!result) return a.origin - b.origin;
      return dir === 'descending' ? -result : result;
    };
  }

  function apply(table) {
    var state = table._sortState, head = headerRow(table);
    if (!state || !head) return;
    var units = group(table, head);
    if (!units || units.list.length < 2) return;
    var ordered = units.list.slice();
    if (state.dir === 'none') {
      ordered.sort(function (a, b) { return a.origin - b.origin; });
    } else {
      var numeric = 0, present = 0;
      ordered.forEach(function (unit) {
        var cell = cellAt(unit.rows[0], state.col);
        unit.key = cell ? dashSortKey(cell.textContent) : null;
        if (unit.key) { present++; if (unit.key.num != null) numeric++; }
      });
      ordered.sort(compare(present && numeric / present >= 0.6 ? 'num' : 'text', state.dir));
    }
    var wanted = [];
    ordered.forEach(function (unit) { wanted.push.apply(wanted, unit.rows); });
    wanted.push.apply(wanted, units.fixed);
    var current = Array.prototype.filter.call(units.parent.children, function (row) {
      return wanted.indexOf(row) >= 0;
    });
    if (current.every(function (row, i) { return row === wanted[i]; })) return;
    var fragment = document.createDocumentFragment();
    wanted.forEach(function (row) { fragment.appendChild(row); });
    units.parent.appendChild(fragment);
    if (observer) observer.takeRecords();
  }

  function storageKey(table, head) {
    var labels = Array.prototype.map.call(head.cells, function (c) {
      return c.textContent.replace(/\s+/g, ' ').trim();
    }).join('|');
    var twins = Array.prototype.filter.call(document.querySelectorAll('table[data-sort-label]'), function (t) {
      return t !== table && t.getAttribute('data-sort-label') === labels &&
        (t.compareDocumentPosition(table) & Node.DOCUMENT_POSITION_FOLLOWING);
    }).length;
    table.setAttribute('data-sort-label', labels);
    return location.pathname + '#' + (table.id || twins) + '|' + labels;
  }

  function saved() {
    try { return JSON.parse(sessionStorage.getItem(STORE) || '{}') || {}; } catch (e) { return {}; }
  }

  function remember(table) {
    var all = saved();
    if (table._sortState.dir === 'none') delete all[table._sortKey];
    else all[table._sortKey] = table._sortState;
    try { sessionStorage.setItem(STORE, JSON.stringify(all)); } catch (e) { /* storage disabled */ }
  }

  function setState(table, head, state) {
    table._sortState = state;
    Array.prototype.forEach.call(head.cells, function (cell) { cell.removeAttribute('aria-sort'); });
    if (state.dir !== 'none') {
      var th = cellAt(head, state.col);
      if (th) th.setAttribute('aria-sort', state.dir);
      table.setAttribute('data-sort-active', '');
    } else {
      table.removeAttribute('data-sort-active');
    }
    apply(table);
  }

  function enhance(table) {
    table.setAttribute('data-sort-ready', '');
    if (table.matches(SKIP)) return;
    var head = headerRow(table);
    if (!head) return;
    var any = false;
    Array.prototype.forEach.call(head.cells, function (cell) {
      var label = cell.textContent.replace(/\s+/g, ' ').trim();
      if (!label || cell.hasAttribute('data-no-sort')) return;
      cell.setAttribute('data-sortable', '');
      if (!cell.hasAttribute('tabindex')) cell.tabIndex = 0;
      if (!cell.title) cell.title = 'Sort by ' + label + ' (click again to reverse, a third time to reset)';
      any = true;
    });
    if (!any) return;
    table._sortKey = storageKey(table, head);
    var state = saved()[table._sortKey];
    if (state && state.dir && typeof state.col === 'number') setState(table, head, state);
  }

  function cycle(th) {
    var table = th.closest('table'), head = table && headerRow(table);
    if (!head || th.parentNode !== head) return;
    var col = columnOf(head, th), state = table._sortState || {};
    var dir = state.col !== col || !state.dir || state.dir === 'none' ? 'ascending'
      : state.dir === 'ascending' ? 'descending' : 'none';
    setState(table, head, { col: col, dir: dir });
    if (table._sortKey) remember(table);
  }

  function scan() {
    document.querySelectorAll('table').forEach(function (table) {
      if (!table.hasAttribute('data-sort-ready')) enhance(table);
      else if (table.hasAttribute('data-sort-active')) apply(table);
    });
  }

  document.addEventListener('click', function (event) {
    var th = event.target.closest && event.target.closest('th[data-sortable]');
    if (!th || event.target.closest('a, button, input, select, textarea, label')) return;
    cycle(th);
  });
  document.addEventListener('keydown', function (event) {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    var th = event.target;
    if (!th.matches || !th.matches('th[data-sortable]')) return;
    event.preventDefault();
    cycle(th);
  });
  function start() {
    scan();
    var pending = false;
    observer = new MutationObserver(function () {
      if (pending) return;
      pending = true;
      setTimeout(function () { pending = false; scan(); }, 60);
    });
    observer.observe(document.body, { childList: true, subtree: true });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();

  window.dashboardTableSort = { sortKey: dashSortKey, refresh: scan };
})();
</script>
"""


def table_sort_script() -> str:
    """Head script; `theme_boot_script()` includes it on every page."""
    return _SCRIPT.replace("__SORT_KEY__", SORT_KEY_JS.strip())


def table_sort_css() -> str:
    """Header affordances; `theme_css()` includes them on every page."""
    return r"""
/* ── Sortable tables ────────────────────────────────────────── */
th[data-sortable] { cursor: pointer; user-select: none; }
th[data-sortable]:hover, th[data-sortable]:focus-visible { color: var(--fg) !important; }
th[data-sortable]::after { content: "\2195"; content: "\2195" / ""; display: inline-block;
                           margin-left: 4px; font-size: .8em; opacity: 0;
                           transition: opacity .12s ease; }
th[data-sortable]:hover::after, th[data-sortable]:focus-visible::after { opacity: .5; }
th[data-sortable][aria-sort="ascending"]::after { content: "\25B2"; content: "\25B2" / ""; opacity: .85; }
th[data-sortable][aria-sort="descending"]::after { content: "\25BC"; content: "\25BC" / ""; opacity: .85; }
"""


__all__ = ["SORT_KEY_JS", "table_sort_css", "table_sort_script"]
