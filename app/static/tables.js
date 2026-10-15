// فلاتر على رأس كل جدول: خانة تصفية لكل عمود (نص أو أرقام: ‎>100 ، ‎<50 ، ‎=0)
(function () {
  var norm = function (x) {
    return (x || '').toLowerCase().replace(/[‎‏‪-‮]/g, '').replace(/[أإآ]/g, 'ا')
      .replace(/ة/g, 'ه').replace(/ى/g, 'ي').replace(/\s+/g, ' ').trim();
  };
  var num = function (x) { var v = parseFloat(String(x).replace(/[,\s]/g, '')); return isNaN(v) ? null : v; };
  var span = function (c) { return +c.getAttribute('colspan') || 1; };
  var width = function (tr) { var w = 0; [].forEach.call(tr.children, function (c) { w += span(c); }); return w; };

  function match(text, q) {
    var m = /^([<>=]=?)\s*(-?[\d.,]+)$/.exec(q);
    if (m) {
      var v = num(text), x = num(m[2]); if (v === null) return false;
      return m[1] === '>' ? v > x : m[1] === '>=' ? v >= x : m[1] === '<' ? v < x : m[1] === '<=' ? v <= x : v === x;
    }
    return norm(text).indexOf(q) >= 0;
  }

  function setup(tbl) {
    var all = [].slice.call(tbl.querySelectorAll(':scope > thead > tr, :scope > tbody > tr, :scope > tr'));
    var head = [];
    var thead = tbl.querySelector(':scope > thead');
    if (thead) head = [].slice.call(thead.children);
    else for (var i = 0; i < all.length && all[i].querySelector(':scope > th') && !all[i].querySelector(':scope > td'); i++) head.push(all[i]);
    if (!head.length) return;
    var body = all.filter(function (tr) { return head.indexOf(tr) < 0; });
    var ncols = 0;
    body.forEach(function (tr) { ncols = Math.max(ncols, width(tr)); });
    var data = body.filter(function (tr) { return width(tr) === ncols && tr.children.length === ncols && !tr.classList.contains('total'); });
    if (ncols < 2 || data.length < 3) return;

    // أسماء الأعمدة من صفوف الرأس (مع مراعاة الدمج)
    var grid = [];
    head.forEach(function (tr, r) {
      grid[r] = grid[r] || []; var c = 0;
      [].forEach.call(tr.children, function (cell) {
        while (grid[r][c]) c++;
        var cs = span(cell), rs = +cell.getAttribute('rowspan') || 1;
        for (var a = 0; a < rs; a++) for (var b = 0; b < cs; b++) { grid[r + a] = grid[r + a] || []; grid[r + a][c + b] = cell; }
        c += cs;
      });
    });
    var leaf = grid[head.length - 1] || [];

    var fr = document.createElement('tr'); fr.className = 'colf noprint';
    var inputs = [];
    for (var c = 0; c < ncols; c++) {
      var th = document.createElement('th'), inp = document.createElement('input');
      var name = leaf[c] ? leaf[c].textContent.trim() : '';
      inp.type = 'search'; inp.placeholder = name ? 'تصفية' : ''; inp.title = name ? 'تصفية: ' + name : '';
      inp.dataset.col = c; inputs.push(inp); th.appendChild(inp); fr.appendChild(th);
    }
    var last = head[head.length - 1];
    last.parentNode.insertBefore(fr, last.nextSibling);
    var others = body.filter(function (tr) { return data.indexOf(tr) < 0; });

    function apply() {
      var fs = inputs.map(function (i) { return [+i.dataset.col, norm(i.value)]; }).filter(function (f) { return f[1]; });
      data.forEach(function (tr) {
        tr.hidden = !fs.every(function (f) { return match(tr.children[f[0]].textContent, f[1]); });
      });
      others.forEach(function (tr) { tr.hidden = fs.length > 0; });   // صفوف الإجمالي تختفي أثناء التصفية
    }
    inputs.forEach(function (i) { i.addEventListener('input', apply); });
  }

  function init() {
    document.querySelectorAll('table.t').forEach(function (t) {
      if (t.classList.contains('lines') || t.classList.contains('nofilter') || t.closest('form') || t.querySelector('tr.colf')) return;
      try { setup(t); } catch (e) { /* جدول غير منتظم — بدون فلتر */ }
    });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();

  // الطباعة (من الزرار أو Ctrl+P): الجداول العريضة تُطبع أفقيًا بعرض الورقة
  function orient() {
    var wide = [].some.call(document.querySelectorAll('main table'), function (t) { return t.scrollWidth > 900; });
    var st = document.getElementById('page-orient');
    if (!st) { st = document.createElement('style'); st.id = 'page-orient'; document.head.appendChild(st); }
    st.textContent = '@page{size:A4 ' + (wide ? 'landscape' : 'portrait') + ';margin:8mm}';
  }
  window.addEventListener('beforeprint', orient);
  window.printPage = function () { orient(); window.print(); };
})();
