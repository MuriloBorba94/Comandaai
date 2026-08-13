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

2. **Pedidos + cozinha.** Portar `Pedido` e seu fluxo de status/timestamps;
   painel `/cozinha` tenant-scoped.

3. **Cupons + bairros de entrega.** Portar `Cupom`/`CupomUso` (padrão de
   reserva de uso) e `BairroEntrega`.

4. **Cobrança recorrente da própria SaaS.** Integrar um gateway brasileiro
   com suporte a PIX recorrente (Asaas recomendado; Iugu/Vindi como
   alternativa — Stripe não é uma boa opção aqui por não cobrir bem PIX
   recorrente no Brasil). Webhooks atualizam `Tenant.status` e
   `Tenant.proxima_cobranca_em`; tenant inadimplente/cancelado é bloqueado
   pelo `before_request` já existente em `app/tenancy.py`.

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

10. **Hardening e operação.** Backup por tenant, audit log, feature-gating
    por plano (`Tenant.plano`), ferramenta de impersonation para suporte,
    monitoramento de erros (ex. Sentry).

11. **Migração do Borba's Burguer como "tenant zero".** Criar o `Tenant` do
    próprio Borba's Burguer, importar os dados reais (`Produto`, `Cupom`,
    `BairroEntrega`, `LojaConfig`) do banco atual, cortar DNS/agente de
    impressão/WhatsApp para o novo sistema, rodar em paralelo por um
    período de transição, e então desativar `C:\borbas_burguer_v17`.
