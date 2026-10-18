/* رسوم التقارير والمبيعات (Chart.js): ألوان خضراء زي المرجع، تدرّج في الأعمدة والمساحات، منحنيات ناعمة وحركة هادية.
   rpCharts(CH, {pal, digits}) — CH من السيرفر (type, labels, datasets, horizontal, stacked, dual, y_title, y1_title, unit). */
(function () {
  // درجات الأخضر (زي المرجع) — كل رسم بياخدها بالترتيب: أول سلسلة أخضر، التانية ليموني، التالتة تركواز ...
  var GREEN = ['#22C55E', '#A3E635', '#14B8A6', '#15803D', '#BEF264', '#2DD4BF', '#4ADE80', '#65A30D', '#0F766E', '#86EFAC'];

  function rgba(hex, a) {
    var h = hex.replace('#', '');
    if (h.length === 3) h = h.split('').map(function (c) { return c + c; }).join('');
    var n = parseInt(h.slice(0, 6), 16);
    return 'rgba(' + (n >> 16 & 255) + ',' + (n >> 8 & 255) + ',' + (n & 255) + ',' + a + ')';
  }

  // تدرّج رأسي (أو أفقي) بيتحسب بعد ما الرسم يعرف مساحته
  function grad(hex, a1, a2, horizontal) {
    return function (ctx) {
      var ch = ctx.chart, area = ch.chartArea;
      if (!area) return rgba(hex, a1);
      var g = horizontal ? ch.ctx.createLinearGradient(area.right, 0, area.left, 0)
                         : ch.ctx.createLinearGradient(0, area.top, 0, area.bottom);
      g.addColorStop(0, rgba(hex, a1)); g.addColorStop(1, rgba(hex, a2));
      return g;
    };
  }

  window.rpCharts = function (CH, opt) {
    opt = opt || {};
    var dark = document.documentElement.dataset.mode === 'dark';
    var root = getComputedStyle(document.documentElement);
    var board = document.querySelector('.dboard');
    var bcss = board ? getComputedStyle(board) : root;
    var txt = ((dark ? bcss.getPropertyValue('--dk-muted') : root.getPropertyValue('--muted')) || '').trim() || '#6b7280';
    var gridC = dark ? 'rgba(255,255,255,.07)' : 'rgba(15,23,42,.07)';
    var card = dark ? ((bcss.getPropertyValue('--dk-card') || '').trim() || '#131B18') : '#ffffff';
    var nf = new Intl.NumberFormat('en', {maximumFractionDigits: opt.digits == null ? 2 : opt.digits});

    Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
    Chart.defaults.color = txt;

    CH.forEach(function (c) {
      var el = document.getElementById(c.id); if (!el) return;
      var has = c.datasets.some(function (ds) { return ds.data.some(function (v) { return v; }); });
      if (!has) { el.parentNode.innerHTML = '<p class="muted rp-empty">لا توجد بيانات في الفترة المحددة</p>'; return; }
      var donut = c.type === 'doughnut', H = !!c.horizontal;
      var fmtTick = function (v) { return nf.format(v); };
      var axisGrid = {color: gridC, drawTicks: false, borderDash: [4, 6]};
      var scales = {};
      if (!donut && H) {
        scales = {x: {reverse: true, beginAtZero: true, grid: axisGrid, border: {display: false},
                      ticks: {callback: fmtTick, padding: 6, maxTicksLimit: 5, maxRotation: 0}},
                  y: {position: 'right', grid: {display: false}, border: {display: false}, ticks: {autoSkip: false, padding: 8}}};
      } else if (!donut) {
        scales = {x: {reverse: true, stacked: !!c.stacked, grid: {display: false}, border: {display: false}, ticks: {padding: 8}},
                  y: {position: 'right', stacked: !!c.stacked, beginAtZero: true, grid: axisGrid, border: {display: false},
                      ticks: {callback: fmtTick, padding: 8, maxTicksLimit: 6}}};
      }

      c.datasets.forEach(function (ds, i) {
        var type = ds.type || c.type;
        if (donut) {
          ds.backgroundColor = ds.data.map(function (v, k) { return GREEN[k % GREEN.length]; });
          ds.borderColor = card; ds.borderWidth = 3; ds.borderRadius = 8; ds.spacing = 2; ds.hoverOffset = 10;
          return;
        }
        var base = GREEN[i % GREEN.length];
        if (type === 'line' && c.dual) base = dark ? '#FDE047' : '#0F766E';   // خط المقياس التاني بلون واضح فوق الأعمدة الخضرا
        if (type === 'bar') {
          ds.backgroundColor = grad(base, 1, c.stacked ? .85 : .45, H);
          ds.hoverBackgroundColor = base;
          ds.borderWidth = 0; ds.borderColor = base;
          ds.maxBarThickness = H ? 30 : 42;
          ds.categoryPercentage = .72; ds.barPercentage = .82;
          ds.borderRadius = c.stacked ? 6 : 999; ds.borderSkipped = c.stacked ? 'start' : 'start';
          ds.order = 1;
        } else if (type === 'line') {
          var area = !c.dual && c.type === 'line';   // رسم خط لوحده = مساحة متدرّجة تحته
          ds.borderColor = base; ds.borderWidth = 3; ds.tension = .42; ds.cubicInterpolationMode = 'monotone';
          ds.fill = area ? 'origin' : false;
          ds.backgroundColor = area ? grad(base, .38, 0, false) : base;
          ds.pointRadius = area ? 0 : 3; ds.pointHoverRadius = 7; ds.pointHitRadius = 14;
          ds.pointBackgroundColor = base; ds.pointBorderColor = card; ds.pointBorderWidth = 2;
          ds.order = 0;   // الخط فوق الأعمدة
        }
      });

      if (c.dual && !donut) {   // مقياس تاني على اليسار للخط بلونه وعنوانه
        var ln = c.datasets.filter(function (ds) { return ds.type === 'line'; })[0];
        var lc = ln ? ln.borderColor : txt;
        scales.y1 = {position: 'left', beginAtZero: true, grid: {drawOnChartArea: false}, border: {display: false},
                     ticks: {callback: fmtTick, color: lc, padding: 8, maxTicksLimit: 6},
                     title: {display: !!c.y1_title, text: c.y1_title, color: lc, font: {weight: 'bold'}}};
        scales.y.title = {display: !!c.y_title, text: c.y_title, color: txt, font: {weight: 'bold'}};
      }

      var total = donut ? c.datasets[0].data.reduce(function (a, b) { return a + b; }, 0) : 0;
      new Chart(el, {
        type: c.type, data: {labels: c.labels, datasets: c.datasets},
        options: {
          responsive: true, maintainAspectRatio: false, indexAxis: H ? 'y' : 'x', locale: 'en',
          cutout: donut ? '68%' : undefined,
          layout: {padding: {top: 6, left: 14, right: 6}},
          animation: {duration: 1200, easing: 'easeOutQuart',
            delay: function (ctx) { return ctx.type === 'data' && ctx.mode === 'default' ? ctx.dataIndex * 60 + ctx.datasetIndex * 140 : 0; }},
          interaction: {mode: donut ? 'nearest' : 'index', intersect: donut},
          plugins: {
            legend: {display: donut || c.datasets.length > 1, position: donut ? 'right' : 'top', align: donut ? 'center' : 'end', rtl: true,
                     labels: {usePointStyle: true, pointStyle: 'circle', boxWidth: 8, boxHeight: 8, padding: 16}},
            tooltip: {rtl: true, backgroundColor: 'rgba(12,18,16,.94)', titleColor: '#fff', bodyColor: '#E5E7EB',
                      borderColor: 'rgba(255,255,255,.08)', borderWidth: 1, padding: 12, cornerRadius: 12, boxPadding: 6,
                      usePointStyle: true, titleFont: {weight: 'bold'},
                      callbacks: {label: function (ctx) {
                        var v = ctx.parsed && typeof ctx.parsed === 'object' ? (H ? ctx.parsed.x : ctx.parsed.y) : ctx.parsed;
                        var pct = donut && total ? ' (' + (100 * v / total).toFixed(1) + '%)' : '';
                        var name = donut ? ctx.label + ': ' : (c.datasets.length > 1 && ctx.dataset.label ? ctx.dataset.label + ': ' : '');
                        return ' ' + name + nf.format(v) + (c.unit ? ' ' + c.unit : '') + pct;
                      }}},
          },
          scales: scales,
        },
      });
    });
  };
})();
