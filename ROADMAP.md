# Roadmap — de esqueleto multi-tenant a SaaS revendável

Fase 0 é este repositório. As fases seguintes portam, uma a uma, a lógica de
negócio hoje presente em `C:\borbas_burguer_v17` (sistema single-tenant do
Borba's Burguer), adaptando cada peça para o modelo multi-tenant.

0. **Núcleo multi-tenant (este esqueleto).** Tenant, autenticação escopada,
   admin da plataforma, uma entidade de exemplo (`Produto`) provando
   isolamento entre tenants.

1. ~~**Cardápio.**~~ **CONCLUÍDA.** `Categoria`, `Adicional` e `Produto`
   tenant-scoped, com upload de foto isolado por tenant e vitrine pública
   agrupada pela ordem que cada tenant define. Duas correções em relação ao
   repo original, que não sobreviveriam ao multi-tenant: a categoria deixou de
   ser texto livre com ordem fixa no código, e o adicional deixou de ser lista
   global (agora cada produto declara os seus, via `produto_adicional`).
   Continua sem carrinho/pedido — isso é a Fase 2.

2. ~~**Pedidos + cozinha.**~~ **CONCLUÍDA.** `Pedido` com `PedidoItem`/
   `PedidoItemAdicional` (nome e preço congelados na venda), máquina de estados
   com timestamps, carrinho e checkout na vitrine, página pública de
   acompanhamento por token, painel `/cozinha` e fluxo de mesa/comanda com mapa
   do salão. Numeração reinicia por tenant e o salão é configurável em
   `/admin/configuracoes` (no original, a faixa de mesas era constante no
   código). Ainda sem taxa por bairro e sem cupom — Fase 3.

3. ~~**Cupons + bairros de entrega.**~~ **CONCLUÍDA.** `Cupom`/`CupomUso` com o
   padrão reservar → usar/liberar, e `BairroEntrega` alimentando taxa e prazo
   por região. Código do cupom e nome do bairro passaram a ser únicos por
   tenant (eram globais). O desconto nunca incide sobre a taxa de entrega nem
   sobre item marcado como combo promocional, e o cupom é reservado na criação
   do pedido, consumido em qualquer avanço de status e liberado no
   cancelamento. A reserva nasce sem prazo: expirar enquanto a cozinha demora
   liberaria o cupom com o desconto já concedido — a expiração volta na Fase 6.

4. **Cobrança recorrente da própria SaaS.** *Núcleo concluído com provedor
   manual.* `Plano` (catálogo com preço) e `Cobranca` (mensalidade por tenant,
   única por competência), com ciclo diário que emite, avalia atraso e suspende
   além da carência — o bloqueio usa o `before_request` já existente em
   `app/tenancy.py`. Telas de planos, cobranças e resumo por tenant, mais
   `flask ciclo-cobranca` para agendar.

   **Falta:** o provedor `asaas`, que exige conta e chave de API. O ponto de
   encaixe é `criar_no_provedor()` em `app/services/faturamento_saas.py`, e o
   webhook que confirma pagamento automaticamente. Enquanto isso, o provedor
   `manual` opera: você recebe o PIX e marca a cobrança como paga.

5. ~~**Infra de produção (1ª passada).**~~ **NO AR desde 20/08/2026.**
   `https://comandaai.app.br` publicado numa VPS da Locaweb (Ubuntu 24.04, 2 GB),
   com Caddy servindo HTTPS e certificado curinga `*.comandaai.app.br` emitido por
   validação DNS no Cloudflare. Kit de publicação em `deploy/`, com passo a passo
   testado numa instalação real.

   Decisões: o apex serve a página inicial e a área da plataforma, cada
   restaurante fica num subdomínio, a aplicação escuta só em `127.0.0.1` (com
   `0.0.0.0` ela ficaria acessível na porta 5000 sem HTTPS, e daria para forjar
   `X-Forwarded-For` e escapar do rate limit de login), e o banco segue em SQLite —
   o sinal para migrar é `database is locked` no log.

   Duas armadilhas que só apareceram na instalação real, e ficaram documentadas:
   o `caddy validate` rodado como root cria o arquivo de log como root e depois o
   serviço não consegue escrever nele; e a Locaweb bloqueia saída na porta 53, o
   que trava a checagem de propagação que o Caddy faz por conta própria (resolvido
   com `propagation_timeout -1`).

   **Falta:** mandar o backup para fora do disco (hoje ele fica na mesma máquina
   que ele deveria proteger), e monitoramento de erros.

6. **PIX por pedido, por tenant.** Portar o fluxo de pagamento do cliente
   final do repo atual, desta vez com uma abstração de provedor de fato
   (corrigindo o `PIX_PROVIDER` do repo atual, que hoje existe na env mas não
   é usado por nenhuma fábrica de provedores).

7. **WhatsApp multi-tenant-seguro.** Usar exclusivamente a API oficial (Meta
   WhatsApp Cloud API) por tenant — abandonar os modos manual e Baileys do
   repo atual, que não escalam nem são seguros para múltiplos clientes
   simultâneos.

8. **Agente de impressão multi-tenant.** Reaproveitar o padrão de
   `agente_impressao/` do repo atual quase sem mudanças (ele já funciona por
   polling, sem precisar de acesso de entrada à rede do restaurante) —
   adicionar `tenant_id` ao token de pareamento, heartbeat e claim.

9. ~~**Estoque/ficha técnica/financeiro.**~~ **CONCLUÍDA.** `Insumo` com custo
   derivado do pacote de compra, `FichaTecnica` por produto,
   `MovimentacaoEstoque` como razão, baixa automática ao confirmar o pedido com
   custo e lucro gravados, estorno no cancelamento e reajuste quando a comanda
   cresce. `Despesa` e `ReceitaAvulsa` (o `Faturamento` do original, renomeado
   porque confundia com o faturamento das vendas) alimentam a tela de resultado.

   Correção sobre o original: lá a compra de insumo podia ser lançada como
   despesa enquanto o custo do mesmo insumo já entrava pelo CMV — o mesmo
   dinheiro contado duas vezes. Aqui não existe categoria de despesa para
   insumo: a compra é entrada de estoque.

10. **Hardening e operação.** Backup (o `deploy/backup.sh` já cobre banco e
    fotos; falta mandar para fora do disco e separar por tenant), audit log,
    ~~feature-gating por plano~~ (**feito junto com a Fase 4**: cada plano marca
    quais recursos libera, e plano não configurado libera tudo para não tirar
    acesso de quem já usa), ferramenta de impersonation para suporte,
    monitoramento de erros (ex. Sentry).

**Layout e produto (fora da numeração).** Feito depois da Fase 9, em várias
rodadas:

- **Importação do Borba's Burguer.** Estrutura, menu e telas da Gestão portadas
  de `gestao_v18.css` / `gestao.html`: shell com sidebar de 246px e commandbar,
  Monitor, Cozinha em kanban, Mesas com o PDV da comanda em modal, Estoque,
  Custos, Financeiro 2.0 (KPIs, gráficos em canvas sem biblioteca externa,
  fluxo de caixa) e o Relatório de Vendas Histórico com exportação CSV. A
  vitrine veio do `index.html` original: banner da loja, busca, cards em lista
  com miniatura e sacola flutuante.

- **Linguagem visual própria.** Depois disso o sistema inteiro foi revestido com
  a linguagem da página inicial: fundo quase preto, superfície em gradiente com
  borda, escuro por padrão e tema claro em `[data-theme="light"]`. Os nomes de
  classe da Gestão foram mantidos, então a estrutura das ~30 telas continua
  valendo — trocou só a pele.

- **Página inicial do produto** (`public/landing.html` + `landing.css`): folha
  própria, demonstração desenhada em CSS, calculadora de comissão, e a seção de
  planos alimentada pelo catálogo real. Nada ali promete recurso que o sistema
  não tem — há teste que falha se PIX automático, WhatsApp ou impressão
  aparecerem no HTML.

- **Identidade por tenant.** `Tenant.logo` e `Tenant.cor_marca`. A cor passa por
  `app/layout.py::cor_valida` antes de entrar no `<style>` (texto livre ali
  seria injeção de CSS), e as variantes de contraste saem derivadas garantindo
  4,5:1 — o restaurante escolhe a cor, não escolhe se ela é legível.

- **Sessão.** Cookie sem validade (morre ao fechar o navegador) e expiração por
  inatividade no servidor, em `app/sessao.py` — é ela que cobre o navegador
  configurado para restaurar sessão ao reabrir.

- **Planos granulares.** 10 recursos e limites numéricos por plano
  (`max_produtos`, `max_mesas`). Plano sem recursos configurados libera tudo, e
  limite em branco significa sem teto: apertar a régua é sempre decisão
  explícita.

A diferença estrutural em relação ao original: lá a Gestão é **uma página só**
com abas. Aqui cada item do menu continua sendo uma rota, porque o bloqueio por
plano é por rota — uma página única carregaria de uma vez os recursos que o
plano do tenant não inclui.

11. **Migração do Borba's Burguer como "tenant zero".** Criar o `Tenant` do
    próprio Borba's Burguer, importar os dados reais (`Produto`, `Cupom`,
    `BairroEntrega`, `LojaConfig`) do banco atual, cortar DNS/agente de
    impressão/WhatsApp para o novo sistema, rodar em paralelo por um
    período de transição, e então desativar `C:\borbas_burguer_v17`.
