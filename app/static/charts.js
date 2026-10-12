// الرسوم الشهرية: تبديل «الإجمالي / مقارنة الفروع / الرواكد» + تلميح فوق العمود (الماوس أو الصباع)
document.querySelectorAll('.mchart').forEach(function (box) {
  var tip = box.querySelector('.tip'), key = 'mc:' + box.id;
  function toEnd() { box.querySelectorAll('.view:not([hidden]) .scroll').forEach(function (sc) { if (sc.scrollWidth > sc.clientWidth) sc.scrollLeft = -sc.scrollWidth; }); }
  function setMode(m) {
    if (!box.querySelector('.view[data-mode="' + m + '"]')) m = 'stk';
    box.querySelectorAll('.view').forEach(function (v) { v.hidden = v.dataset.mode !== m; });
    box.querySelectorAll('.seg-tg button').forEach(function (b) { b.classList.toggle('on', b.dataset.m === m); });
    try { localStorage.setItem(key, m); } catch (e) {}
    toEnd();
  }
  box.querySelectorAll('.seg-tg button').forEach(function (b) { b.addEventListener('click', function () { tip.hidden = true; setMode(b.dataset.m); }); });
  try { var saved = localStorage.getItem(key); if (saved) setMode(saved); } catch (e) {}
  toEnd();
  function show(g) {
    var parts = g.dataset.tip.split('|');
    tip.innerHTML = '<b>' + parts[0] + '</b><div class="tv">' + parts[1] + '</div>' + parts.slice(2).map(function (x) {
      var p = x.split('~'); return '<div class="ts">' + (p[0] !== '' ? '<i class="sw s' + p[0] + '"></i>' : '') + p[1] + '</div>'; }).join('');
    tip.hidden = false;
    var r = box.getBoundingClientRect(), hr = g.querySelector('.hit').getBoundingClientRect();
    var x = hr.left + hr.width / 2 - r.left, w = tip.offsetWidth;
    tip.style.left = Math.max(4, Math.min(r.width - w - 4, x - w / 2)) + 'px';
    tip.style.top = (g.closest('.view').offsetTop) + 'px';
    g.closest('svg').querySelectorAll('.bar').forEach(function (b) { b.classList.toggle('dim', b !== g); });
  }
  box.querySelectorAll('.bar').forEach(function (g) {
    g.addEventListener('pointerenter', function () { show(g); });
    g.addEventListener('click', function (e) { if (g.dataset.url && !e.target.closest('a')) location.href = g.dataset.url; else show(g); });
  });
  box.addEventListener('pointerleave', function () { tip.hidden = true; box.querySelectorAll('.bar').forEach(function (b) { b.classList.remove('dim'); }); });
});
