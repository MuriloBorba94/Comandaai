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

4. ~~**Cobrança recorrente da própria SaaS.**~~ **CONCLUÍDA.** `Plano`
   (catálogo com preço) e `Cobranca` (mensalidade por tenant, única por
   competência), com ciclo diário que emite, avalia atraso e suspende além da
   carência — o bloqueio usa o `before_request` já existente em
   `app/tenancy.py`. Telas de planos, cobranças e resumo por tenant, mais
   `flask ciclo-cobranca` para agendar.

   O provedor `asaas` entrou no encaixe que já existia
   (`criar_no_provedor`), com a mesma abstração de provedor do PIX e do
   WhatsApp, e o webhook `/webhooks/asaas` confirma o pagamento. O laço que
   fecha a fase: emitir → suspender por atraso → o restaurante paga → o acesso
   volta sozinho, sem ninguém no meio.

   Escolha por tenant, não global: virar a chave de todos de uma vez é como se
   descobre no dia seguinte que faltava o CNPJ de metade deles. O `manual`
   continua sendo o piso e não desaparece.

   Três decisões que valem mais que o código:

   - **Emitir nunca falha por causa do gateway.** Se o Asaas estiver fora do ar
     no dia do ciclo, a cobrança é criada assim mesmo e o motivo fica na
     observação. Mês sem cobrança não bloqueia ninguém — só some com a receita
     da plataforma em silêncio. `flask reemitir-no-gateway` é a segunda chance.
   - **O webhook recusa tudo sem token configurado.** Não existe modo "aberto
     para facilitar o teste" num endereço público que marca dinheiro como
     recebido. Reenvio do mesmo evento responde 200, para a fila do Asaas não
     girar para sempre.
   - **Estorno não bloqueia sozinho.** Fica registrado e visível; derrubar a
     loja de alguém por causa de um webhook é um martelo grande demais para uma
     decisão que sempre tem contexto humano atrás.

   O ambiente padrão é o **sandbox** do Asaas — conta de testes gratuita e
   completa. Configuração pela metade não sai cobrando ninguém de verdade. O
   passo a passo está em `deploy/COBRANCA-AUTOMATICA.md`.

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

   O mecanismo de backup para fora do disco e o monitoramento entraram na Fase
   10. **Falta a configuração**, que só pode ser feita no servidor:
   `BACKUP_REMOTO` no `.env` e um monitor externo apontado para `/saude`.

6. ~~**PIX por pedido, por tenant.**~~ **CONCLUÍDA.** A chave PIX é do
   restaurante e fica no `Tenant`: o dinheiro cai direto na conta dele e a
   plataforma não é intermediária de pagamento em momento nenhum. `Pagamento`
   guarda o que foi cobrado, o BR Code congelado e quem confirmou o
   recebimento.

   A abstração de provedor que faltava agora existe de verdade
   (`services/pagamentos/registro.py`): um provedor é uma classe com quatro
   respostas, e quem cobra nunca sabe qual está atendendo. No repo antigo o
   `PIX_PROVIDER` estava na env e nenhuma fábrica o lia — o código chamava a
   InfinitePay direto, com `if provider == "infinitepay"` em três arquivos.

   **Só um provedor entrou, e por decisão baseada em dado.** No banco em
   produção do sistema antigo há 162 pagamentos online: **159 pelo PIX direto**
   (uso contínuo, até a véspera desta fase) e **3 pelo gateway**, os três no
   mesmo dia de julho e nunca repetidos. Portar o gateway seria carregar código
   que ninguém usa e que eu não teria como testar de verdade, sem conta e sem
   chave. A porta está aberta; o provedor entra quando houver restaurante que
   precise.

   Correções sobre o original:

   - **O BR Code era gerado em dois lugares** (`pix_service.py` e dentro de
     `routes/public.py`) e as duas cópias já tinham divergido. Agora é uma
     função só, com o CRC conferido contra o valor de teste da especificação e
     o código lido de volta campo a campo nos testes.
   - **Pagamento e pedido podiam divergir.** Lá o atendente conseguia avançar
     um pedido de "Aguardando PIX" sem marcar o pagamento, e o financeiro
     continuava dizendo que ninguém pagou um pedido já entregue. Aqui existe um
     caminho só para sair de "Aguardando PIX": confirmar o recebimento, que
     marca o pagamento e move o pedido na mesma operação.
   - **"Borba's Burguer" saía "BORBA S BURGUER"** na tela do banco, porque o
     apóstrofo virava espaço.

   Pedido esperando PIX não baixa estoque, não imprime comanda e não avança por
   botão de status.

7. ~~**WhatsApp multi-tenant-seguro.**~~ **CONCLUÍDA.** `Notificacao` por
   pedido, com o texto congelado no disparo, e provedor escolhido por tenant.

   **Baileys ficou de fora, como o roteiro mandava.** Ele funciona conectando-se
   ao WhatsApp pessoal do número, o que viola os termos e faz o número ser
   banido. Num sistema de um restaurante só, o dono assume esse risco por conta
   própria; numa plataforma, um banimento derruba o atendimento de quem não
   escolheu nada.

   O roteiro dizia "exclusivamente a API oficial", e aí houve um desvio
   deliberado: **entraram dois provedores, não um.** A API oficial custa por
   mensagem e exige conta de negócios verificada, o que leva dias — um
   restaurante novo ficaria sem avisar ninguém nesse meio-tempo. O provedor
   `link` (wa.me) cobre esse vão: o sistema escreve a mensagem, alguém do
   balcão clica, e não custa nada. Não é o modo "manual" do repo antigo, que
   só montava um link solto sem registrar nada: aqui o aviso é uma linha na
   fila, com quem clicou e quando.

   Quem escolhe é o restaurante, na tela. A única regra automática é de
   segurança: quem pediu "meta" mas não terminou de configurar volta para o
   link, porque aviso que não sai é pior do que aviso que exige um clique.

   Correções sobre o original: `cliente.split()[0]` estourava com nome em
   branco, e o cliente recebia o `id` global do pedido em vez do número que ele
   vê na tela.

   **`Plano.libera_tudo` nasceu aqui**, e resolve um problema que já tinha
   custado duas migrations de dados (Fases 8 e 6): recurso novo não entrava em
   plano com `recursos` preenchido, e sumia da tela de quem pagava pelo plano
   mais caro. Agora o plano pode dizer que é completo, e o próximo recurso entra
   sozinho.

8. ~~**Agente de impressão multi-tenant.**~~ **CONCLUÍDA.** `AgenteImpressao`
   por tenant (token pareado, guardado só como hash, com heartbeat) e
   `ImpressaoJob` como fila. O agente em `agente/` continua funcionando por
   consulta — a rede do restaurante nunca recebe conexão de fora — e agora fala
   com o subdomínio do próprio restaurante: o código precisa bater com o tenant
   daquele endereço, então são duas travas, não uma.

   Duas mudanças em relação ao original, e as duas por defeito dele:

   - **Não existe modo local.** Lá o Flask rodava na mesma máquina da
     impressora; aqui o servidor está num datacenter. Um seletor "local ou
     remoto" só ofereceria uma opção que nunca funciona.
   - **A fila é uma tabela, não campos no `Pedido`.** Com `print_status` no
     pedido só cabe uma impressão por pedido: quando a mesa pedia mais uma
     porção, reimprimir mandava o pedido inteiro e o cozinheiro repetia o que
     já tinha entregado. Cada trabalho congela o texto do momento em que foi
     criado, então a comanda de acréscimo leva só o que entrou.

   Quando o papel sai: no primeiro avanço do pedido do site (imprimir na
   chegada gastaria bobina com pedido que o atendente ainda vai recusar), na
   abertura da comanda de mesa, a cada item lançado na mesa, e no botão
   Imprimir do painel da cozinha. Cancelar o pedido tira da fila o que ainda
   não saiu, e não mexe no que já saiu.

   **Por que não imprimir direto na impressora padrão do Windows** (pergunta que
   volta): porque o sistema do tenant não roda em máquina nenhuma do
   restaurante — roda na VPS, em Linux, num datacenter. Não existe caminho entre
   um servidor em outra cidade e uma impressora USB num balcão; as alternativas
   seriam abrir porta no roteador do cliente e exigir IP fixo, que é caro,
   frágil e inseguro. No sistema antigo o modo local existia porque o Flask
   rodava na mesma máquina Windows da impressora — e, mesmo lá, o
   `impressao_modo` do Borba's estava em `remoto`: ele já usava o agente.

   **Por que não imprimir pelo navegador**, que está no computador certo:
   funciona, e foi considerado como alternativa sem instalação para restaurante
   novo. O que derruba é o modo de falhar: exige a tela da cozinha aberta
   naquele computador e um clique por pedido, e se a aba fechar ou a máquina
   dormir os pedidos param de sair **em silêncio**. O agente fica rodando atrás
   e o painel mostra "Conectado". Decisão de 21/08/2026 — reabrir se aparecer
   um restaurante que não consiga instalar o agente.

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

10. **Hardening e operação.** *Em andamento.*

    ~~**Feature-gating por plano**~~ — feito junto com a Fase 4.

    ~~**Backup.**~~ A parte delicada (copiar e CONFERIR) saiu do script de shell
    e virou `flask fazer-backup` / `flask verificar-backup`, dentro da
    aplicação: o caminho do banco vem da configuração em vez de escrito à mão, e
    a suíte exercita a conferência — inclusive provando que ela REPROVA backup
    corrompido e backup vazio. Descoberta pelo caminho: `integrity_check` do
    SQLite confere a estrutura do banco, não o conteúdo byte a byte, então um
    arquivo corrompido no disco passava nele; quem pega isso é a soma de
    verificação gravada ao lado. Cópia para fora da máquina via `BACKUP_REMOTO`
    (rclone), com falha barulhenta quando configurada e quebrada — ver
    `deploy/BACKUP-FORA-DO-DISCO.md`. Separar por tenant continua em aberto.

    ~~**Audit log.**~~ `Auditoria` grava pouco de propósito: dinheiro, acesso e
    configuração que muda para onde o dinheiro vai. Cadastro de produto e
    movimento de estoque ficam de fora — encher o diário do que é rotina é o
    jeito mais eficiente de tornar inútil o registro do que é raro. Registrar
    nunca derruba a operação registrada. Tela por restaurante e tela geral na
    plataforma.

    ~~**Monitoramento.**~~ `/saude` responde 200/503 e **nada mais** para quem
    pergunta de fora — resposta detalhada em endereço aberto conta a estranhos
    qual é o banco e quanto disco resta. O quadro completo fica na plataforma.
    Só banco fora do ar e publicação pela metade contam como grave; disco cheio
    e fila parada são avisos, porque alarme que dispara por qualquer coisa é
    alarme que se aprende a ignorar.

    ~~**Impersonation para suporte.**~~ O cookie de sessão é por host, e é isso
    que impede uma sessão de valer em outro restaurante — então a plataforma
    não consegue simplesmente criar sessão no subdomínio do cliente.
    Compartilhar o cookie entre subdomínios resolveria e seria muito pior:
    derrubaria a separação que protege um restaurante do outro em todo o resto
    do sistema. A ponte é um `PasseSuporte` de uso único, válido por 2 minutos
    e preso a um restaurante.

    Ele não fica seguro por ser restrito, e sim por ser curto, visível e
    registrado: faixa âmbar em TODA página enquanto durar, sessão que termina
    sozinha em 30 minutos (o relógio não para enquanto a pessoa mexe), e tudo o
    que for feito lá dentro sai no diário com o nome de quem da plataforma
    entrou — não com o do dono do restaurante. Não há restrição de ação de
    propósito: suporte que só enxerga e não conserta não resolve o problema de
    ninguém, e uma lista de "o que o suporte não pode" daria uma sensação de
    proteção que a primeira exceção derrubaria.

    **Falta:** backup separado por tenant.

**Mapa do salão (fora da numeração).** Feito em 22/08/2026, a partir de uma
imagem de referência de outro sistema.

- **Quatro estados por cor**: disponível, em consumo, pediu a conta e ociosa
  (10 min sem ninguém pedir nada). Mais tempo desde a abertura e valor no
  cartão, e a contagem de cada estado na legenda — que é o número que o dono
  olha primeiro.

- `ultimo_consumo_em` existe em vez de reaproveitar `updated_at` porque este
  muda por qualquer coisa (troca de status, reimpressão), e "faz 10 minutos que
  ninguém pede nada" precisa significar exatamente isso, senão a cor mente.
  Lançar item reinicia o relógio E desfaz o "pediu a conta": quem pede mais uma
  cerveja não está mais esperando para ir embora.

- **Mesa saiu do cardápio.** A vitrine já só oferecia Entrega e Retirada, mas
  tela não é trava: um POST montado à mão abria comanda numa mesa qualquer,
  inclusive numa já ocupada, e ela aparecia no mapa como se um cliente tivesse
  sentado ali. Agora `criar_pedido` exige `permitir_mesa=True`, que só as duas
  rotas do salão passam — argumento, e não campo do payload, porque campo vem
  do mesmo formulário de que se quer desconfiar. E o padrão é negar, para um
  caminho novo nascer fechado.

- **Duas correções vieram de medir, não de olhar.** Branco sobre o âmbar dava
  2,2:1 — ilegível — e eu tinha escrito no CSS que as quatro cores eram escuras
  o bastante. O âmbar passou a levar texto escuro (9,4:1), como placa de aviso
  no mundo real; verde e azul foram escurecidos 8% para chegar a 4,5:1. E, como
  as quatro cores têm luminosidade parecida, quem tem daltonismo não separaria
  verde de vermelho: cada cartão passou a escrever o estado ao lado da cor,
  ocupando o lugar do "1 comanda" que era sempre 1 e não informava nada.

**Equipe e entregas (fora da numeração).** Feito em 21/08/2026, depois da
pergunta "não dá para mapear uma rota de entrega a partir de uma
geolocalização?".

- **Tela de equipe.** Antes dela todo usuário nascia `admin` e só pela criação
  do tenant — não havia como cadastrar um entregador sem SSH. Três papéis
  (admin, atendente, entregador) e duas travas que impedem alguém de se trancar
  para fora: o último admin ativo não pode se rebaixar nem se desativar.
  Ninguém é excluído, só desativado, porque o nome de quem lançou item na
  comanda está espalhado pelo histórico. Isso também destravou o limite
  `max_usuarios`, que estava no catálogo sem limitar nada.

- **Entregas.** Tela do entregador com endereço, botão de rota, telefone e
  baixa. A rota **não usa geocodificação nossa**: o endereço digitado é
  entregue ao aplicativo de mapa do próprio celular. Testei os cinco bairros
  que o Borba's atende num geocodificador gratuito e nenhum foi encontrado —
  endereço de cidade pequena é descrito por referência, não por rua e número
  mapeáveis. Quem sabe ler "perto da igreja" é uma pessoa.

- **Rastreio.** O mapa voltou, com uma correção de desenho vinda do dado: no
  sistema antigo, de 774 entregas só 37 registraram posição, e nenhuma depois
  de 16/08. A causa provável é que a tela do entregador só servia ao cliente —
  alguém tinha que manter o celular aberto em benefício de outra pessoa. Aqui
  ela é a ferramenta de trabalho dele, e a posição vai junto porque a tela já
  está aberta. Se ainda assim morrer, a resposta é tirar o rastreio, não
  insistir.

  A posição é do PEDIDO, não da pessoa: cada envio sobrescreve o anterior (não
  há trajeto guardado), some quando a entrega termina, e o mapa só aparece com
  leitura de menos de 5 minutos — ponto parado por celular sem sinal faria o
  cliente concluir que o entregador empacou. O Leaflet é servido do próprio
  domínio: a página é aberta no 3G da rua, e CDN fora do ar deixaria o cliente
  sem mapa, além de mostrar a um terceiro quem acompanha qual pedido.

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

11. ~~**Migração do Borba's Burguer como "tenant zero".**~~ **NO AR desde
    21/08/2026**, em `borbas.comandaai.app.br`. `flask importar-legado` trouxe
    loja, cardápio com fotos, adicionais, bairros, cupons, insumos, fichas
    técnicas e usuários **com a senha de sempre** — o hash de senha é o mesmo
    formato nos dois sistemas, então ninguém precisou trocar nada.

    O histórico de pedidos ficou de fora de propósito: no sistema antigo os
    itens de um pedido são um texto solto no registro, não linhas de uma
    tabela. Importar os 1101 pedidos criaria mil registros sem item, sem
    custo e sem lucro — exatamente o que alimenta o CMV, o "mais vendidos" e
    a margem. Um financeiro cheio de números errados, parecendo certo. O
    histórico continua disponível no sistema antigo para consulta.

    Junto veio o `flask remover-tenant`, porque migração sem desfazer não é
    migração: a importação recusa slug repetido, então um erro na primeira
    tentativa deixaria o restaurante torto e sem volta.

    **Falta:** rodar em paralelo pelo tempo que você achar necessário e então
    desativar `C:\borbas_burguer_v17`. É decisão sua, não técnica.
