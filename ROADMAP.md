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

5. **Infra de produção (1ª passada).** Trocar SQLite por Postgres via
   `DATABASE_URL`, DNS de subdomínio real + HTTPS, ambientes separados de
   dev/staging/prod, logging e monitoramento básico.

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

9. **Estoque/ficha técnica/financeiro.** Portar `Insumo`/`FichaTecnica`/
   `MovimentacaoEstoque`/`Faturamento`/`Despesa`. Prioridade mais baixa: o
   valor de revenda do produto vem primeiro de pedidos + cozinha + WhatsApp +
   cobrança.

10. **Hardening e operação.** Backup por tenant, audit log,
    ~~feature-gating por plano~~ (**feito junto com a Fase 4**: cada plano marca
    quais recursos libera, e plano não configurado libera tudo para não tirar
    acesso de quem já usa), ferramenta de impersonation para suporte,
    monitoramento de erros (ex. Sentry).

11. **Migração do Borba's Burguer como "tenant zero".** Criar o `Tenant` do
    próprio Borba's Burguer, importar os dados reais (`Produto`, `Cupom`,
    `BairroEntrega`, `LojaConfig`) do banco atual, cortar DNS/agente de
    impressão/WhatsApp para o novo sistema, rodar em paralelo por um
    período de transição, e então desativar `C:\borbas_burguer_v17`.
