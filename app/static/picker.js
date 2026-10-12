/* قائمة منسدلة لاختيار الصنف (بدل datalist اللي ما بتشتغلش كويس على الموبايل).
   أي input عليه list="..." بيتحوّل تلقائياً: بحث بالكود أو الاسم، وبيظهر تحته أقرب 40 نتيجة. */
(function () {
  var norm = function (x) { return (x || '').toLowerCase().replace(/[أإآ]/g, 'ا').replace(/ة/g, 'ه').replace(/ى/g, 'ي').trim(); };
  var box = document.createElement('div'); box.className = 'pick-box'; box.hidden = true; document.body.appendChild(box);
  var cur = null, act = -1;
  function opts(inp) {
    var dl = document.getElementById(inp.dataset.list); if (!dl) return [];
    return [].map.call(dl.options, function (o) { return o.value; });
  }
  function place() {
    if (!cur) return;
    var r = cur.getBoundingClientRect();
    box.style.top = (r.bottom + window.scrollY + 2) + 'px';
    box.style.left = (r.left + window.scrollX) + 'px';
    box.style.width = Math.max(r.width, 260) + 'px';
    if (r.left + Math.max(r.width, 260) > innerWidth - 8) box.style.left = (innerWidth - Math.max(r.width, 260) - 8 + window.scrollX) + 'px';
  }
  function show(inp) {
    cur = inp; act = -1;
    var words = norm(inp.value).split(/\s+/).filter(Boolean);
    var list = opts(inp).filter(function (v) { var n = norm(v); return words.every(function (w) { return n.indexOf(w) >= 0; }); }).slice(0, 40);
    if (!list.length) { box.hidden = true; return; }
    box.innerHTML = list.map(function (v) { return '<div class="pick-o" data-v="' + v.replace(/"/g, '&quot;') + '">' + v.replace(/</g, '&lt;') + '</div>'; }).join('');
    box.hidden = false; place();
  }
  function choose(v) {
    if (!cur) return;
    cur.value = v; box.hidden = true;
    cur.dispatchEvent(new Event('input', { bubbles: true })); cur.dispatchEvent(new Event('change', { bubbles: true }));
  }
  document.addEventListener('focusin', function (e) {
    var t = e.target;
    if (t.matches && t.matches('input[list]')) { t.dataset.list = t.getAttribute('list'); t.removeAttribute('list'); t.setAttribute('autocomplete', 'off'); }
    if (t.matches && t.matches('input[data-list]')) show(t);
  });
  document.addEventListener('input', function (e) { if (e.target.matches && e.target.matches('input[data-list]') && e.isTrusted) show(e.target); });
  document.addEventListener('keydown', function (e) {
    if (box.hidden || !cur || e.target !== cur) return;
    var os = box.querySelectorAll('.pick-o');
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault(); act = (act + (e.key === 'ArrowDown' ? 1 : -1) + os.length) % os.length;
      os.forEach(function (o, i) { o.classList.toggle('on', i === act); }); os[act].scrollIntoView({ block: 'nearest' });
    } else if (e.key === 'Enter' && act >= 0) { e.preventDefault(); choose(os[act].dataset.v); }
    else if (e.key === 'Escape') box.hidden = true;
  });
  box.addEventListener('mousedown', function (e) { e.preventDefault(); });   // ما يقفلش الـ input قبل الاختيار
  box.addEventListener('click', function (e) { var o = e.target.closest('.pick-o'); if (o) choose(o.dataset.v); });
  document.addEventListener('focusout', function (e) { if (e.target === cur) setTimeout(function () { box.hidden = true; }, 150); });
  window.addEventListener('resize', place); window.addEventListener('scroll', place, true);
})();
