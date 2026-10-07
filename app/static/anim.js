/* حركات البرنامج — GSAP + ScrollTrigger (موجودين جوه البرنامج في static/vendor) */
(function () {
  var root = document.documentElement;
  function done() { window.__animReady = true; root.classList.remove('gsap-on'); }
  if (!window.gsap || !root.classList.contains('gsap-on')) { done(); return; }
  window.__animReady = true;
  if (window.ScrollTrigger) gsap.registerPlugin(ScrollTrigger);

  var fine = window.matchMedia && matchMedia('(hover: hover) and (pointer: fine)').matches;
  var EASE = 'power3.out';

  // ---------- صفحة الدخول: الكارت بيدخل 3D والخلفية بتتحرك مع الماوس ----------
  var auth = document.querySelector('.auth');
  if (auth) {
    // رسمة الكسارة والخلاطة عنصر لوحده عشان تتحرك مع الماوس
    var art = document.createElement('div');
    art.className = 'auth-art';
    document.body.prepend(art);
    document.body.classList.add('auth-js');
    gsap.set(auth, { visibility: 'visible', transformPerspective: 1200, transformOrigin: '50% 100%' });
    var parts = [];
    auth.querySelectorAll(':scope > img, :scope > h1, :scope > p, form > *').forEach(function (el) { if (parts.indexOf(el) < 0) parts.push(el); });
    var tl = gsap.timeline({ onComplete: function () { gsap.set(auth, { clearProps: 'transform' }); done(); } });
    tl.from(art, { y: 80, autoAlpha: 0, duration: 1.4, ease: 'expo.out' }, 0)
      .from(auth, { autoAlpha: 0, rotationX: 28, rotationY: -18, z: -220, y: 60, duration: 1.2, ease: 'expo.out' }, 0.1)
      .from(parts, { autoAlpha: 0, y: 18, stagger: 0.07, duration: 0.6, ease: EASE, clearProps: 'transform' }, '-=0.75');
    gsap.to(auth.querySelector('img'), { y: -4, duration: 1.6, ease: 'sine.inOut', yoyo: true, repeat: -1, delay: 1.4 });
    if (fine) {  // الخلفية بس اللي بتتحرك مع الماوس — الكارت ثابت عشان الكتابة والضغط
      var ax = gsap.quickTo(art, 'x', { duration: 1.2, ease: 'power2.out' });
      window.addEventListener('pointermove', function (e) { ax(-(e.clientX / innerWidth - 0.5) * 40); });
    }
    return;
  }

  // ---------- الهيدر: مرة واحدة في الجلسة ----------
  var first = false;
  try { first = !sessionStorage.getItem('hdrAnim'); sessionStorage.setItem('hdrAnim', '1'); } catch (_) {}
  if (first) {
    gsap.from('header.top', { yPercent: -100, duration: 0.7, ease: EASE });
    gsap.from('nav.main a, header.top .who, header.top img, header.top .title', { autoAlpha: 0, y: -10, stagger: 0.04, duration: 0.5, delay: 0.25, ease: EASE });
  }

  // ---------- محتوى الصفحة: بيدخل واحد ورا التاني بميلة 3D خفيفة ----------
  var items = [];
  document.querySelectorAll('main > *').forEach(function (el) {
    if (el.classList.contains('grid')) el.querySelectorAll(':scope > .card').forEach(function (c) { items.push(c); });
    else items.push(el);
  });
  var now = [], later = [];
  items.forEach(function (el) { (el.getBoundingClientRect().top < innerHeight * 0.95 ? now : later).push(el); });
  gsap.set(items, { visibility: 'visible', transformPerspective: 900, transformOrigin: '50% 0%' });
  var from = { autoAlpha: 0, y: 26, rotationX: -10 };
  var to = { autoAlpha: 1, y: 0, rotationX: 0, duration: 0.7, ease: EASE, clearProps: 'transform' };
  gsap.set(items, from);
  gsap.to(now, Object.assign({}, to, { stagger: 0.07, onComplete: done }));
  if (!now.length) done();
  if (later.length && window.ScrollTrigger) {
    ScrollTrigger.batch(later, { start: 'top 92%', once: true, onEnter: function (b) { gsap.to(b, Object.assign({}, to, { stagger: 0.08 })); } });
  } else if (later.length) gsap.to(later, to);

  // صفوف الجداول اللي باينة: بتنزل ورا بعض
  document.querySelectorAll('main table.t:not(.lines):not(.cards)').forEach(function (t) {
    var rows = [].filter.call(t.querySelectorAll('tr'), function (tr) { return !tr.querySelector('th'); }).slice(0, 25);
    if (rows.length) gsap.from(rows, { autoAlpha: 0, x: 14, duration: 0.45, stagger: 0.025, delay: 0.25, ease: 'power2.out', clearProps: 'all' });
  });

  // ---------- الأرقام في لوحة المتابعة بتعدّ ----------
  document.querySelectorAll('.kpi .v').forEach(function (el, i) {
    var txt = el.textContent.trim();
    if (!/^-?[\d,]+(\.\d+)?$/.test(txt)) return;
    var target = parseFloat(txt.replace(/,/g, '')), dec = (txt.split('.')[1] || '').length;
    if (!target) return;
    var o = { v: 0 };
    el.textContent = '0';
    gsap.to(o, { v: target, duration: 1.4, delay: 0.25 + i * 0.06, ease: 'power3.out',
      onUpdate: function () { el.textContent = o.v.toLocaleString('en-US', { maximumFractionDigits: dec }); },
      onComplete: function () { el.textContent = txt; } });
    gsap.from(el, { scale: 0.6, duration: 0.8, delay: 0.25 + i * 0.06, ease: 'back.out(2)' });
  });

  // ---------- شريط الترحيب ----------
  var hero = document.querySelector('.hero');
  if (hero) {
    var art = hero.querySelector('.hero-art'), txt = hero.querySelector('.hero-txt');
    gsap.from(art, { x: -80, autoAlpha: 0, duration: 1.2, ease: 'expo.out', delay: 0.1 });
    gsap.from(txt.children, { x: 40, autoAlpha: 0, stagger: 0.12, duration: 0.8, ease: EASE, delay: 0.25 });
  }

  // ---------- 3D: الكروت بتميل مع الماوس ----------
  if (fine) {
    document.querySelectorAll('.kpi, .hero').forEach(function (el) {
      var max = el.classList.contains('hero') ? 4 : 10;
      gsap.set(el, { transformPerspective: 800 });
      var rx = gsap.quickTo(el, 'rotationX', { duration: 0.5, ease: 'power2.out' });
      var ry = gsap.quickTo(el, 'rotationY', { duration: 0.5, ease: 'power2.out' });
      var sc = gsap.quickTo(el, 'scale', { duration: 0.5, ease: 'power2.out' });
      var art2 = el.querySelector('.hero-art'), ax = art2 && gsap.quickTo(art2, 'x', { duration: 0.8, ease: 'power2.out' });
      el.addEventListener('pointermove', function (e) {
        var r = el.getBoundingClientRect(), nx = (e.clientX - r.left) / r.width - 0.5, ny = (e.clientY - r.top) / r.height - 0.5;
        rx(-ny * max); ry(nx * max); sc(el.classList.contains('hero') ? 1 : 1.03);
        if (ax) ax(nx * 24);
      });
      el.addEventListener('pointerleave', function () { rx(0); ry(0); sc(1); if (ax) ax(0); });
    });
  }

  // ---------- الأزرار: ضغطة بحركة نابضة ----------
  document.addEventListener('pointerdown', function (e) {
    var b = e.target.closest && e.target.closest('.btn');
    if (b) gsap.fromTo(b, { scale: 0.94 }, { scale: 1, duration: 0.5, ease: 'elastic.out(1.1, 0.5)', clearProps: 'scale' });
  });

  // رسائل التنبيه
  gsap.from('.flash', { autoAlpha: 0, y: -16, scale: 0.97, duration: 0.6, ease: 'back.out(1.6)' });
})();
