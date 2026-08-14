/* Menu lateral e alternador de tema do painel.
 *
 * Portado de gestao_v18.js / gestao.html do sistema single-tenant. O tema fica
 * no localStorage do navegador, então é preferência de quem opera — não do
 * restaurante. Duas pessoas do mesmo tenant podem usar temas diferentes, e a
 * escolha de uma nunca chega ao outro tenant.
 */
(function () {
  "use strict";

  var CHAVE_TEMA = "comandaai_tema";

  function elemento(id) {
    return document.getElementById(id);
  }

  // ---- menu lateral (só aparece como gaveta em telas estreitas) ----
  function abrirMenu() {
    elemento("sidebar")?.classList.add("aberta");
    elemento("sidebar-fundo")?.classList.add("aberta");
    document.body.classList.add("nav-aberto");
  }

  function fecharMenu() {
    elemento("sidebar")?.classList.remove("aberta");
    elemento("sidebar-fundo")?.classList.remove("aberta");
    document.body.classList.remove("nav-aberto");
  }

  // ---- tema ----
  function aplicarRotulo() {
    var botao = elemento("alternar-tema");
    if (!botao) return;
    var escuro = document.documentElement.getAttribute("data-theme") === "dark";
    botao.textContent = escuro ? "☀ Claro" : "🌙 Escuro";
    botao.setAttribute("aria-pressed", escuro ? "true" : "false");
  }

  function alternarTema() {
    var escuro = document.documentElement.getAttribute("data-theme") === "dark";
    var proximo = escuro ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", proximo);
    try {
      localStorage.setItem(CHAVE_TEMA, proximo);
    } catch (e) {
      /* navegador sem localStorage: o tema vale só nesta página */
    }
    aplicarRotulo();
  }

  document.addEventListener("DOMContentLoaded", function () {
    elemento("fab-menu")?.addEventListener("click", abrirMenu);
    elemento("sidebar-fundo")?.addEventListener("click", fecharMenu);
    elemento("alternar-tema")?.addEventListener("click", alternarTema);
    aplicarRotulo();

    // Fecha a gaveta ao navegar, senão ela fica aberta por cima da página nova.
    document.querySelectorAll(".nav a").forEach(function (link) {
      link.addEventListener("click", fecharMenu);
    });
  });

  document.addEventListener("keydown", function (evento) {
    if (evento.key === "Escape") fecharMenu();
  });
})();
