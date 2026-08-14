/* Painel do Comanda ai — portado de gestao_v18.js do Borba's Burguer.
 *
 * Os gráficos do Financeiro são desenhados à mão em <canvas>, como no original:
 * nenhuma biblioteca externa, nenhum CDN. As cores saem das variáveis CSS, então
 * eles acompanham o tema e a cor de marca de cada tenant.
 *
 * Diferença em relação ao original: lá a Gestão era uma página só com abas, e o
 * gráfico era redesenhado ao trocar de aba. Aqui cada tela é uma rota, então
 * basta desenhar quando a página do Financeiro carrega.
 */
(() => {
  'use strict';

  const CHAVE_TEMA = 'comandaai_tema';
  const money = value => new Intl.NumberFormat('pt-BR', {style: 'currency', currency: 'BRL', maximumFractionDigits: 0}).format(value || 0);
  const chartState = {data: null, resizeTimer: null};
  const el = id => document.getElementById(id);

  // ---------------------------------------------------------------- menu ----
  function openNav() {
    el('v17-sidebar')?.classList.add('open');
    el('v17-sidebar-backdrop')?.classList.add('open');
    document.body.classList.add('nav-open');
  }

  function closeNav() {
    el('v17-sidebar')?.classList.remove('open');
    el('v17-sidebar-backdrop')?.classList.remove('open');
    document.body.classList.remove('nav-open');
  }

  window.openNav = openNav;
  window.closeNav = closeNav;

  // ---------------------------------------------------------------- tema ----
  function aplicarRotuloTema() {
    const botao = el('theme-toggle');
    if (!botao) return;
    const escuro = document.documentElement.getAttribute('data-theme') === 'dark';
    botao.textContent = escuro ? '☀ Claro' : '🌙 Escuro';
    botao.setAttribute('aria-pressed', escuro ? 'true' : 'false');
  }

  function toggleTheme() {
    const escuro = document.documentElement.getAttribute('data-theme') === 'dark';
    const proximo = escuro ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', proximo);
    try { localStorage.setItem(CHAVE_TEMA, proximo); } catch (e) { /* sem localStorage: vale só nesta página */ }
    aplicarRotuloTema();
    renderFinanceCharts();
  }

  window.toggleTheme = toggleTheme;

  // --------------------------------------------------------------- toast ----
  window.showToast = function (message, timeout = 3200) {
    const toast = el('toast');
    if (!toast) return;
    toast.textContent = message;
    toast.style.display = 'block';
    clearTimeout(window.__toastTimer);
    window.__toastTimer = setTimeout(() => { toast.style.display = 'none'; }, timeout);
  };

  // ------------------------------------------------------------- gráficos ---
  function prepareCanvas(canvas) {
    if (!canvas || !canvas.parentElement) return null;
    const rect = canvas.parentElement.getBoundingClientRect();
    if (rect.width < 20 || rect.height < 20) return null;
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.round(rect.width * ratio);
    canvas.height = Math.round(rect.height * ratio);
    canvas.style.width = rect.width + 'px';
    canvas.style.height = rect.height + 'px';
    const ctx = canvas.getContext('2d');
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    return {ctx, width: rect.width, height: rect.height};
  }

  function roundedRect(ctx, x, y, width, height, radius) {
    const r = Math.min(radius, Math.abs(width) / 2, Math.abs(height) / 2);
    ctx.beginPath();
    ctx.roundRect ? ctx.roundRect(x, y, width, height, r) : ctx.rect(x, y, width, height);
  }

  function chartColors() {
    const style = getComputedStyle(document.documentElement);
    const v = (name, fallback) => (style.getPropertyValue(name) || fallback).trim();
    return {
      grid: v('--border', '#e5e7eb'),
      axis: v('--muted', '#6b7280'),
      bar: v('--brand', '#c8102e'),
      line: v('--brand-2', '#c2620a'),
      dotStroke: v('--panel', '#fff'),
    };
  }

  function drawRevenueChart() {
    const prepared = prepareCanvas(el('finance-revenue-chart'));
    if (!prepared || !chartState.data) return;
    const {ctx, width, height} = prepared;
    const colors = chartColors();
    const chart = chartState.data.chart || {labels: [], revenue: [], profit: []};
    const labels = chart.labels || [];
    const revenue = chart.revenue || [];
    const profit = chart.profit || [];
    ctx.clearRect(0, 0, width, height);

    const padding = {top: 18, right: 18, bottom: 38, left: 54};
    const plotW = Math.max(1, width - padding.left - padding.right);
    const plotH = Math.max(1, height - padding.top - padding.bottom);
    const all = [...revenue, ...profit, 0];
    const maxValue = Math.max(...all, 1);
    const minValue = Math.min(...all, 0);
    const range = Math.max(1, maxValue - minValue);
    const yFor = value => padding.top + (maxValue - value) / range * plotH;
    const baseline = yFor(0);

    ctx.font = '11px Segoe UI, Arial';
    ctx.textAlign = 'right';
    ctx.textBaseline = 'middle';
    for (let i = 0; i <= 4; i++) {
      const value = minValue + range * (i / 4);
      const y = yFor(value);
      ctx.strokeStyle = colors.grid; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(padding.left, y); ctx.lineTo(width - padding.right, y); ctx.stroke();
      ctx.fillStyle = colors.axis; ctx.fillText(money(value), padding.left - 8, y);
    }

    if (!labels.length) {
      ctx.fillStyle = colors.axis; ctx.textAlign = 'center';
      ctx.fillText('Sem dados no período', width / 2, height / 2);
      return;
    }

    const step = plotW / labels.length;
    const barWidth = Math.max(5, Math.min(28, step * 0.52));
    labels.forEach((label, index) => {
      const x = padding.left + step * index + step / 2;
      const valueY = yFor(revenue[index] || 0);
      const barTop = Math.min(valueY, baseline);
      const barH = Math.max(2, Math.abs(baseline - valueY));
      ctx.fillStyle = colors.bar;
      roundedRect(ctx, x - barWidth / 2, barTop, barWidth, barH, 4);
      ctx.fill();
      const showEvery = labels.length > 18 ? Math.ceil(labels.length / 10) : 1;
      if (index % showEvery === 0) {
        ctx.fillStyle = colors.axis; ctx.textAlign = 'center'; ctx.textBaseline = 'top';
        ctx.fillText(label, x, height - padding.bottom + 12);
      }
    });

    ctx.strokeStyle = colors.line; ctx.lineWidth = 2.2; ctx.lineJoin = 'round'; ctx.lineCap = 'round';
    ctx.beginPath();
    profit.forEach((value, index) => {
      const x = padding.left + step * index + step / 2;
      const y = yFor(value || 0);
      index ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
    });
    ctx.stroke();
    profit.forEach((value, index) => {
      const x = padding.left + step * index + step / 2;
      const y = yFor(value || 0);
      ctx.fillStyle = colors.line;
      ctx.beginPath(); ctx.arc(x, y, 3.2, 0, Math.PI * 2); ctx.fill();
      ctx.strokeStyle = colors.dotStroke; ctx.lineWidth = 1.5; ctx.stroke();
    });
  }

  function drawExpenseChart() {
    const prepared = prepareCanvas(el('finance-expense-chart'));
    if (!prepared || !chartState.data) return;
    const {ctx, width, height} = prepared;
    const colors = chartColors();
    const expenses = chartState.data.expenses || [];
    ctx.clearRect(0, 0, width, height);
    const total = expenses.reduce((sum, item) => sum + (Number(item.value) || 0), 0);
    const centerX = width / 2, centerY = height / 2;
    const radius = Math.min(width, height) * 0.43, inner = radius * 0.58;
    if (!total) {
      ctx.strokeStyle = colors.grid; ctx.lineWidth = radius - inner;
      ctx.beginPath(); ctx.arc(centerX, centerY, (radius + inner) / 2, 0, Math.PI * 2); ctx.stroke();
      return;
    }
    let start = -Math.PI / 2;
    expenses.forEach(item => {
      const angle = (Number(item.value) || 0) / total * Math.PI * 2;
      ctx.beginPath();
      ctx.arc(centerX, centerY, radius, start, start + angle);
      ctx.arc(centerX, centerY, inner, start + angle, start, true);
      ctx.closePath();
      ctx.fillStyle = item.color || colors.bar;
      ctx.fill();
      start += angle;
    });
  }

  function loadFinanceData() {
    const node = el('finance-chart-data');
    if (!node) return;
    try {
      chartState.data = JSON.parse(node.textContent);
    } catch (error) {
      console.error('Dados financeiros inválidos', error);
    }
  }

  function renderFinanceCharts() {
    if (!el('finance-chart-data')) return;
    if (!chartState.data) loadFinanceData();
    drawRevenueChart();
    drawExpenseChart();
  }

  // ------------------------------------------------------- fluxo de caixa ---
  function setupCashFlowFilters() {
    const buttons = document.querySelectorAll('[data-flow-filter]');
    const rows = document.querySelectorAll('#finance-flow-table tbody tr[data-flow-type]');
    buttons.forEach(button => button.addEventListener('click', () => {
      buttons.forEach(item => item.classList.remove('active'));
      button.classList.add('active');
      const filter = button.dataset.flowFilter;
      rows.forEach(row => { row.hidden = filter !== 'Todos' && row.dataset.flowType !== filter; });
    }));
  }

  // ---------------------------------------------------------------- boot ----
  document.addEventListener('keydown', event => { if (event.key === 'Escape') closeNav(); });

  document.addEventListener('DOMContentLoaded', () => {
    el('v17-fab-menu')?.addEventListener('click', openNav);
    el('v17-sidebar-backdrop')?.addEventListener('click', closeNav);
    el('theme-toggle')?.addEventListener('click', toggleTheme);
    aplicarRotuloTema();

    // Fecha a gaveta ao navegar, senão ela fica aberta por cima da página nova.
    document.querySelectorAll('.v17-nav .tab-btn').forEach(link => link.addEventListener('click', closeNav));

    loadFinanceData();
    setupCashFlowFilters();
    setTimeout(renderFinanceCharts, 60);
    window.addEventListener('resize', () => {
      clearTimeout(chartState.resizeTimer);
      chartState.resizeTimer = setTimeout(renderFinanceCharts, 140);
    });
  });
})();
