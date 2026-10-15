/* Hisaab shared UI behaviour: theme, tabs, copy/download, dropzones, mobile sheet,
   and a thin layer over Chart.js so charts follow the current theme. */
(function () {
  'use strict';
  const root = document.documentElement;
  const UI = (window.HisaabUI = {});
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private mode */ } },
  };

  /* ---------- theme ---------- */
  const THEME_KEY = 'hisaab_theme';
  const dark = matchMedia('(prefers-color-scheme: dark)');
  const effective = () => root.dataset.theme || (dark.matches ? 'dark' : 'light');
  function syncMode() { root.dataset.mode = effective(); }
  UI.setTheme = function (theme) {
    root.dataset.theme = theme;
    store.set(THEME_KEY, theme);
    syncMode();
    requestAnimationFrame(rebuildCharts);
  };
  dark.addEventListener('change', () => { if (!store.get(THEME_KEY)) { syncMode(); rebuildCharts(); } });
  syncMode();

  /* ---------- helpers for charts ---------- */
  UI.css = (name) => getComputedStyle(root).getPropertyValue(name).trim();
  UI.alpha = (hex, a) => {
    const m = /^#?([\da-f]{2})([\da-f]{2})([\da-f]{2})$/i.exec(hex);
    return m ? `rgba(${parseInt(m[1], 16)}, ${parseInt(m[2], 16)}, ${parseInt(m[3], 16)}, ${a})` : hex;
  };
  UI.areaFill = (hex, top = 0.26) => (ctx) => {
    const area = ctx.chart.chartArea;
    if (!area) return 'transparent';
    const g = ctx.chart.ctx.createLinearGradient(0, area.top, 0, area.bottom);
    g.addColorStop(0, UI.alpha(hex, top));
    g.addColorStop(1, UI.alpha(hex, 0));
    return g;
  };
  UI.fmt = {
    full: (symbol) => (v) => symbol + Math.round(v).toLocaleString(),
    short: (symbol) => (v) => {
      const a = Math.abs(v), s = v < 0 ? '-' : '';
      if (symbol === '₹') {
        if (a >= 1e7) return s + '₹' + (a / 1e7).toFixed(1).replace(/\.0$/, '') + 'Cr';
        if (a >= 1e5) return s + '₹' + (a / 1e5).toFixed(1).replace(/\.0$/, '') + 'L';
      } else {
        if (a >= 1e6) return s + symbol + (a / 1e6).toFixed(1).replace(/\.0$/, '') + 'M';
      }
      if (a >= 1e3) return s + symbol + (a / 1e3).toFixed(a >= 1e4 ? 0 : 1).replace(/\.0$/, '') + 'k';
      return s + symbol + Math.round(a);
    },
  };

  let builders = [], live = [];
  function prepareCharts() {
    if (!window.Chart) return;
    const C = Chart.defaults;
    C.font.family = getComputedStyle(document.body).fontFamily;
    C.font.size = 12;
    C.color = UI.css('--text-3');
    C.borderColor = UI.css('--grid');
    C.scale.grid.color = UI.css('--grid');
    C.scale.border.display = false;
    // Adjust the library's animation defaults in place: replacing the whole object strips keys it relies on
    // (this is what made line charts throw "this._fn is not a function" whenever animations were on).
    if (matchMedia('(prefers-reduced-motion: reduce)').matches) C.animation = false;
    else if (C.animation) { C.animation.duration = 650; C.animation.easing = 'easeOutQuart'; }
    Object.assign(C.plugins.legend.labels, {
      usePointStyle: true, pointStyle: 'rectRounded', boxWidth: 9, boxHeight: 9, padding: 18, color: UI.css('--text-2'),
    });
    Object.assign(C.plugins.tooltip, {
      backgroundColor: UI.css('--surface'), titleColor: UI.css('--text'), bodyColor: UI.css('--text-2'),
      borderColor: UI.css('--border-strong'), borderWidth: 1, padding: 12, cornerRadius: 12, boxPadding: 5,
      usePointStyle: true, titleFont: { weight: '650', size: 12 }, bodyFont: { size: 12 },
    });
  }
  UI.chart = (el, config) => { prepareCharts(); const c = new Chart(el, config); live.push(c); return c; };
  UI.mount = (build) => { builders.push(build); build(); };
  UI.resizeCharts = () => live.forEach((c) => c.resize());
  function rebuildCharts() {
    if (!builders.length) return;
    live.forEach((c) => c.destroy());
    live = [];
    builders.forEach((b) => b());
  }

  /* ---------- wiring that needs the DOM ---------- */
  document.addEventListener('DOMContentLoaded', () => {
    // theme buttons
    document.querySelectorAll('[data-theme-toggle]').forEach((btn) =>
      btn.addEventListener('click', () => UI.setTheme(effective() === 'dark' ? 'light' : 'dark')));

    // privacy (demo) mode: blurs money on the investments page
    const eye = document.getElementById('privacybtn');
    if (eye) {
      const on = () => document.body.classList.contains('privacy');
      const paint = () => {
        eye.setAttribute('aria-pressed', on());
        eye.title = on() ? 'Show amounts' : 'Hide amounts (demo mode)';
        eye.querySelector('.i-eye').hidden = on();
        eye.querySelector('.i-eye-off').hidden = !on();
      };
      eye.addEventListener('click', () => {
        document.body.classList.toggle('privacy');
        store.set('hisaab_privacy', on() ? '1' : '0');
        paint();
      });
      paint();
    }

    // mobile "More" sheet
    const toggleSheet = (open) => document.body.classList.toggle('sheet-open', open);
    document.querySelectorAll('[data-sheet-toggle]').forEach((b) => b.addEventListener('click', () => toggleSheet()));
    document.querySelectorAll('.sheet-backdrop').forEach((b) => b.addEventListener('click', () => toggleSheet(false)));
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') toggleSheet(false); });

    // popover menus close when you click elsewhere
    document.addEventListener('click', (e) => {
      document.querySelectorAll('details.menu[open]').forEach((d) => { if (!d.contains(e.target)) d.open = false; });
    });

    // copy / download a textarea's text
    document.querySelectorAll('[data-copy]').forEach((btn) => btn.addEventListener('click', async () => {
      const box = document.querySelector(btn.dataset.copy);
      try { await navigator.clipboard.writeText(box.value); } catch (e) { box.select(); document.execCommand('copy'); }
      const note = document.querySelector(btn.dataset.note || '[data-copied]');
      if (note) { note.textContent = 'Copied'; setTimeout(() => (note.textContent = ''), 2200); }
    }));
    document.querySelectorAll('[data-download]').forEach((btn) => btn.addEventListener('click', () => {
      const box = document.querySelector(btn.dataset.download);
      const url = URL.createObjectURL(new Blob([box.value], { type: 'text/plain' }));
      const a = Object.assign(document.createElement('a'), { href: url, download: btn.dataset.filename || 'download.txt' });
      document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
    }));

    // drag-and-drop file zones
    document.querySelectorAll('.dropzone').forEach((zone) => {
      const input = zone.querySelector('input[type=file]');
      const label = zone.querySelector('.dz-name');
      input.addEventListener('change', () => { label.textContent = input.files[0] ? input.files[0].name : ''; });
      ['dragenter', 'dragover'].forEach((ev) => zone.addEventListener(ev, () => zone.classList.add('drag')));
      ['dragleave', 'drop'].forEach((ev) => zone.addEventListener(ev, () => zone.classList.remove('drag')));
    });

    // tabs: <div data-tabs data-default="overview"> buttons[role=tab][data-tab]; sections [data-panel]
    document.querySelectorAll('[data-tabs]').forEach((box) => {
      const tabs = [...box.querySelectorAll('[role=tab]')];
      const panels = [...document.querySelectorAll('[data-panel]')];
      const names = tabs.map((t) => t.dataset.tab);
      function show(name, remember) {
        if (!names.includes(name)) name = box.dataset.default || names[0];
        tabs.forEach((t) => t.setAttribute('aria-selected', t.dataset.tab === name));
        panels.forEach((p) => { p.hidden = p.dataset.panel !== name; });
        if (remember) history.replaceState(null, '', '#' + name);
        window.dispatchEvent(new CustomEvent('tabshown', { detail: name }));
        requestAnimationFrame(() => UI.resizeCharts());
      }
      tabs.forEach((t) => t.addEventListener('click', () => show(t.dataset.tab, true)));
      document.querySelectorAll('[data-tab-link]').forEach((a) =>
        a.addEventListener('click', (e) => { e.preventDefault(); show(a.dataset.tabLink, true); window.scrollTo({ top: 0, behavior: 'smooth' }); }));
      show(location.hash.slice(1) || box.dataset.start || box.dataset.default, false);
    });
  });
})();
