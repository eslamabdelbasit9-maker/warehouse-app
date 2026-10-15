// فلاتر على رأس كل جدول: خانة تصفية لكل عمود بأسلوب الـ ERP (* ? ! , .. > <)
(function () {
  var norm = function (x) {
    return (x || '').toLowerCase().replace(/[‎‏‪-‮]/g, '').replace(/[أإآ]/g, 'ا')
      .replace(/ة/g, 'ه').replace(/ى/g, 'ي').replace(/\s+/g, ' ').trim();
  };
  var num = function (x) { var v = parseFloat(String(x).replace(/[,\s]/g, '')); return isNaN(v) ? null : v; };
  var span = function (c) { return +c.getAttribute('colspan') || 1; };
  var width = function (tr) { var w = 0; [].forEach.call(tr.children, function (c) { w += span(c); }); return w; };

  // تصفية بأسلوب الـ ERP:
  //   بحص        يحتوي على «بحص»
  //   بحص*       يبدأ بـ «بحص»        *بوصه   ينتهي بـ «بوصه»      ب*3/4   يبدأ بـ «ب» وينتهي بـ «3/4»
  //   ?          حرف واحد أيًّا كان     RM-G?   ← RM-G1 ، RM-G3 ...
  //   !بحص       لا يحتوي (نفي)        أ,ب     أ أو ب
  //   >100  <=50  =0   مقارنة أرقام      10..50  من 10 إلى 50 (أو من تاريخ إلى تاريخ)
  function one(t, q) {
    var v, x, m;
    if ((m = /^(.*?)\.\.(.*)$/.exec(q))) {            // مدى
      var a = m[1].trim(), b = m[2].trim(), na = num(a), nb = num(b);
      if ((a === '' || na !== null) && (b === '' || nb !== null) && !/^\d{4}-/.test(a + b)) {
        v = num(t); if (v === null) return false;
        return (a === '' || v >= na) && (b === '' || v <= nb);
      }
      return (a === '' || t >= a) && (b === '' || t <= b);   // نص/تاريخ (YYYY-MM-DD)
    }
    if ((m = /^([<>]=?|=)\s*(-?[\d.,]+)$/.exec(q))) {    // مقارنة
      v = num(t); x = num(m[2]); if (v === null) return false;
      return m[1] === '>' ? v > x : m[1] === '>=' ? v >= x : m[1] === '<' ? v < x : m[1] === '<=' ? v <= x : v === x;
    }
    if (/[*?]/.test(q)) {                                  // نجمة وعلامة استفهام
      var re = q.replace(/[.+^${}()|[\]\\]/g, '\\$&').replace(/\*/g, '.*').replace(/\?/g, '.');
      return new RegExp('^' + re + '$').test(t);
    }
    return t.indexOf(q) >= 0;
  }
  function match(text, q) {
    var t = norm(text);
    return q.split(/\s*,\s*(?!\d{3}(?!\d))/).filter(function (p) { return p !== ''; }).some(function (p) {
      var neg = p.charAt(0) === '!'; if (neg) p = p.slice(1).trim();
      var r = p === '' ? t === '' : one(t, p);
      return neg ? !r : r;
    });
  }
  window.erpMatch = function (text, q) { q = norm(q); return !q || match(text, q); };

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
      inp.type = 'search'; inp.placeholder = name ? 'تصفية' : ''; inp.title = name ? 'تصفية: ' + name + '\n* أي حروف   ? حرف واحد   ! نفي   , أو   .. من إلى   > < مقارنة' : '';
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
