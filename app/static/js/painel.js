/* Painel do Comanda ai — portado de gestao_v18.js do Borba's Burguer.
 *
 * Os gráficos do Financeiro são desenhados à mão em <canvas>, como no original:
 * nenhuma biblioteca externa, nenhum CDN. As cores saem das variáveis CSS, então
 * eles acompanham o tema sem precisar repetir nenhum hex aqui.
 *
 * Diferença em relação ao original: lá a Gestão era uma página só com abas, e o
 * gráfico era redesenhado ao trocar de aba. Aqui cada tela é uma rota, então
 * basta desenhar quando a página do Financeiro carrega.
 */
(() => {
  'use strict';

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

  // ------------------------------------------------------- painel de menu ----
  /* Favoritos e historico vivem no navegador. Nao viram tabela de proposito:
   * sao preferencia de atalho de UMA pessoa numa MAQUINA, nao dado do
   * restaurante — e no servidor custariam migration, consulta por pagina e uma
   * decisao sobre o que fazer quando dois atendentes dividem o mesmo login. */
  const CHAVE_FAV = "comandaai_menu_favoritos";
  const CHAVE_HIST = "comandaai_menu_historico";
  const LIMITE_HIST = 6;

  function lerLista(chave) {
    try {
      const bruto = JSON.parse(localStorage.getItem(chave) || "[]");
      return Array.isArray(bruto) ? bruto.filter(x => x && x.rotulo && x.href) : [];
    } catch (e) { return []; }
  }

  function gravarLista(chave, lista) {
    try { localStorage.setItem(chave, JSON.stringify(lista)); } catch (e) {}
  }

  function desenharListaCurta(caixa, lista, vazio) {
    if (!caixa) { return; }
    if (!lista.length) { caixa.innerHTML = '<p class="muted">' + vazio + "</p>"; return; }
    caixa.textContent = "";
    lista.forEach(item => {
      const a = document.createElement("a");
      a.href = item.href;
      a.textContent = item.rotulo;
      a.title = item.rotulo;
      caixa.appendChild(a);
    });
  }

  function marcarEstrelas(favoritos) {
    const nomes = new Set(favoritos.map(f => f.rotulo));
    document.querySelectorAll("[data-favoritar]").forEach(botao => {
      const marcado = nomes.has(botao.dataset.favoritar);
      botao.classList.toggle("marcada", marcado);
      botao.textContent = marcado ? "★" : "☆";
      botao.setAttribute("aria-label", (marcado ? "Desfavoritar " : "Favoritar ") + botao.dataset.favoritar);
    });
  }

  function desenharAtalhos() {
    const favoritos = lerLista(CHAVE_FAV);
    desenharListaCurta(el("menu-favoritos"), favoritos,
      "Passe o mouse num item e clique na estrela.");
    desenharListaCurta(el("menu-historico"), lerLista(CHAVE_HIST),
      "As últimas telas abertas aparecem aqui.");
    marcarEstrelas(favoritos);
  }

  function alternarFavorito(rotulo, href) {
    const favoritos = lerLista(CHAVE_FAV);
    const i = favoritos.findIndex(f => f.rotulo === rotulo);
    if (i >= 0) { favoritos.splice(i, 1); } else { favoritos.push({ rotulo: rotulo, href: href }); }
    gravarLista(CHAVE_FAV, favoritos);
    desenharAtalhos();
  }

  /* Registra a tela ATUAL, e nao a clicada: so entra no historico o que
   * realmente abriu. Clique que virou erro 403 ou pagina inexistente nao
   * merece virar atalho. */
  function registrarVisita() {
    const ativo = document.querySelector(".menu-linha.ativo > a");
    if (!ativo) { return; }
    const item = { rotulo: ativo.dataset.rotulo, href: ativo.getAttribute("href") };
    const lista = lerLista(CHAVE_HIST).filter(x => x.rotulo !== item.rotulo);
    lista.unshift(item);
    gravarLista(CHAVE_HIST, lista.slice(0, LIMITE_HIST));
  }

  function filtrarMenu(termo) {
    const alvo = (termo || "").trim().toLowerCase();
    let achou = 0;
    document.querySelectorAll(".menu-secao").forEach(secao => {
      let visiveis = 0;
      secao.querySelectorAll(".menu-linha").forEach(linha => {
        const bate = !alvo || (linha.dataset.busca || "").includes(alvo);
        linha.hidden = !bate;
        if (bate) { visiveis++; }
      });
      secao.hidden = visiveis === 0;
      achou += visiveis;
    });
    const vazio = el("menu-vazio");
    if (vazio) { vazio.hidden = achou > 0; }
  }

  function menuAberto() { const p = el("menu-painel"); return p && !p.hidden; }

  function abrirMenu() {
    const painel = el("menu-painel");
    if (!painel) { return; }
    painel.hidden = false;
    el("v17-nav-toggle")?.setAttribute("aria-expanded", "true");
    desenharAtalhos();
    const busca = el("menu-painel-busca");
    if (busca) { busca.value = ""; filtrarMenu(""); busca.focus(); }
  }

  function fecharMenu() {
    const painel = el("menu-painel");
    if (!painel) { return; }
    painel.hidden = true;
    el("v17-nav-toggle")?.setAttribute("aria-expanded", "false");
  }

  function alternarMenu() { menuAberto() ? fecharMenu() : abrirMenu(); }

  function ligarMenu() {
    if (!el("menu-painel")) { return; }
    el("v17-nav-toggle")?.addEventListener("click", evento => {
      evento.stopPropagation();
      alternarMenu();
    });
    el("menu-painel-busca")?.addEventListener("input", evento => filtrarMenu(evento.target.value));

    document.querySelectorAll("[data-favoritar]").forEach(botao => {
      botao.addEventListener("click", evento => {
        evento.preventDefault();
        evento.stopPropagation();
        const linha = botao.closest(".menu-linha");
        alternarFavorito(botao.dataset.favoritar, linha.querySelector("a").getAttribute("href"));
      });
    });

    // Clique fora fecha; dentro, nao — a pessoa pode estar digitando na busca.
    document.addEventListener("click", evento => {
      if (!menuAberto()) { return; }
      if (evento.target.closest("#menu-painel") || evento.target.closest("#v17-nav-toggle")) { return; }
      fecharMenu();
    });
    document.addEventListener("keydown", evento => {
      if (evento.key !== "Escape" || !menuAberto()) { return; }
      fecharMenu();
      el("v17-nav-toggle")?.focus();
    });

    registrarVisita();
  }

  /* A alternância claro/escuro saiu daqui junto com o botão da barra: o tema
     do sistema é um só, e um interruptor que não muda nada é pior que nenhum.
     Os gráficos leem as variáveis CSS na hora de desenhar, então não sobrou
     nada para reagir a uma troca de tema. */

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

  // --------------------------------------------------------------------- //
  // Janelinha da barra do dia
  //
  // <details> abre e fecha no clique, mas nao fecha quando a pessoa clica em
  // outro lugar — e uma caixa flutuante que so fecha no mesmo botao nao parece
  // uma aparicao, parece um painel que travou aberto por cima da pagina.
  // --------------------------------------------------------------------- //
  function fecharJanelinhas(exceto) {
    document.querySelectorAll('.bd-acao[open]').forEach(item => {
      if (item !== exceto) { item.open = false; }
    });
  }

  function ligarJanelinhas() {
    document.addEventListener('click', evento => {
      const dentro = evento.target.closest('.bd-acao');
      fecharJanelinhas(dentro);
    });
    document.addEventListener('keydown', evento => {
      if (evento.key !== 'Escape') { return; }
      const aberta = document.querySelector('.bd-acao[open]');
      if (!aberta) { return; }
      fecharJanelinhas(null);
      // Devolve o foco ao botao: quem fechou com o teclado precisa saber onde
      // parou, senao o foco volta para o inicio do documento.
      aberta.querySelector('summary')?.focus();
    });
  }

  document.addEventListener('DOMContentLoaded', () => {
    ligarJanelinhas();
    ligarMenu();
    el('v17-sidebar-backdrop')?.addEventListener('click', closeNav);

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
