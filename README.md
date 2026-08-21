# Comanda ai — núcleo multi-tenant

Este projeto é o **esqueleto** do sistema multi-tenant que vai substituir, aos
poucos, o sistema single-tenant de `C:\borbas_burguer_v17`. Ele não toca em
nada daquele repositório — é uma base nova, isolada, para construir o produto
revendável por assinatura mensal.

O que já existe aqui:
- Modelo de `Tenant` (cada restaurante cliente) identificado por subdomínio.
- Autenticação de `Usuario` escopada por tenant (dois tenants podem ter
  usuários com o mesmo `username`, sem conflito).
- Um `PlatformAdmin` separado — o super-admin (você, o revendedor) que cria e
  gerencia os tenants, sem se misturar com os admins de cada loja. A área da
  plataforma abre numa página inicial com receita recorrente, o que está vencido,
  trials terminando e quais restaurantes pararam de receber pedidos.
- Rate limit por IP nas rotas de login, contando somente tentativas que falham.
- **Sessão que não fica salva**: o cookie de login não tem validade, então o
  navegador o descarta ao fechar; e o servidor derruba a sessão sozinha depois
  de `SESSION_IDLE_MINUTES` sem uso (padrão 4 h). Ver `app/sessao.py`.
- **Cardápio completo por tenant** (Fase 1): `Categoria` ordenável, `Adicional`
  vinculado por produto, `Produto` com foto, e vitrine pública agrupada na
  ordem que cada tenant define.
- **Pedidos, cozinha e mesa** (Fase 2): carrinho e checkout na vitrine, página
  pública de acompanhamento por token, painel `/cozinha` com máquina de estados,
  e comanda de mesa com mapa do salão. O painel se atualiza sozinho: consulta
  `/cozinha/eventos` a cada 8 s e só recarrega a tela quando a fila muda, com
  aviso sonoro opcional para pedido novo.
- **Cupons e taxa por bairro** (Fase 3): cupom com reserva de uso (não vende
  além do limite em checkouts simultâneos), e taxa/prazo de entrega por bairro.
- **Cobrança da assinatura** (Fase 4): catálogo de planos com preço,
  mensalidade por tenant, ciclo que suspende quem passa da carência e libera ao
  registrar o pagamento. Dois provedores, escolhidos **por restaurante**:
  `manual` (você recebe o PIX e marca como pago) e `asaas`, que emite a fatura
  e confirma o pagamento por webhook — o acesso do restaurante volta sozinho
  quando ele paga. Ativação em [`deploy/COBRANCA-AUTOMATICA.md`](deploy/COBRANCA-AUTOMATICA.md).
- **Relatórios de venda** e **estoque com ficha técnica** (Fase 9): custo e lucro
  por pedido, baixa automática de insumo ao vender, despesas a pagar e resultado
  do período.
- **PIX pelo site** (Fase 6): o cliente escolhe “PIX online” no checkout e
  recebe o código e o QR na própria tela de acompanhamento. A chave é **do
  restaurante** — o dinheiro cai direto na conta dele e a plataforma não é
  intermediária de pagamento. O pedido fica em *Aguardando PIX* e só desce para
  a cozinha quando alguém aperta “Recebi o PIX”: pagamento e status mudam na
  mesma operação, então não têm como divergir.
- **Aviso no WhatsApp** (Fase 7): mensagem para o cliente a cada etapa do
  pedido. Dois caminhos, escolhidos por restaurante: o **link** (grátis — o
  sistema escreve, alguém do balcão clica) e a **API oficial da Meta** (envia
  sozinha, cobrada por mensagem na conta do restaurante). O modo não-oficial do
  sistema antigo ficou de fora de propósito: ele faz o número ser banido, e num
  SaaS o banimento atinge quem não escolheu nada.
- **Impressão na cozinha** (Fase 8): comanda no papel pela térmica do balcão. O
  agente em `agente/` roda no computador do restaurante e **consulta** o
  servidor — a rede dele nunca recebe conexão de fora, então não precisa de IP
  fixo nem de porta aberta no roteador. Pareamento por código (guardado só como
  hash), fila com reserva para a comanda não sair duas vezes, e comanda de
  acréscimo que leva só o item que acabou de ser lançado na mesa.
- **Planos versáteis**: cada plano marca quais dos 13 recursos libera (ou se
  libera tudo, inclusive o que for criado depois) e pode
  impor limites numéricos (produtos no cardápio, mesas do salão). Plano sem
  recursos configurados libera tudo; limite em branco ou zero significa sem
  teto — apertar a régua é sempre uma decisão explícita.
- **Layout do Borba's Burguer portado**: painel claro/escuro com sidebar e
  commandbar, vitrine escura para o cliente final, e identidade por tenant —
  cada restaurante envia a sua logo e escolhe a cor da marca em
  `/admin/configuracoes`, sem interferir no vizinho.
- Migrations versionadas com Flask-Migrate/Alembic desde o início.

## Identidade visual de cada tenant

O design system inteiro vive em `app/static/css/comanda.css` e usa **a mesma
linguagem visual da página inicial** (`landing.css`): fundo quase preto, superfície
em gradiente sutil com borda, raios de 8/10/14/18, tipografia com tracking
negativo em título e realce em gradiente da cor da marca. A landing é uma vitrine
para ler uma vez; o painel é ferramenta de oito horas por dia, então o que muda é
a densidade — espaçamento mais apertado e tipografia menor.

**Escuro é o padrão**, que é a cara do produto. O tema claro continua existindo em
`[data-theme="light"]`, guardado no `localStorage` de quem opera — quem trabalha
em cozinha clara precisa dele.

Os nomes de classe vêm do `gestao_v18.css` do Borba's Burguer (`v17-app-shell`,
`card-admin`, `admin-input`, `btn-add/save/secondary/delete`, `metric-grid`,
`mesas-grid`, `v17-kanban`, `finance-*`, `comanda-*`) e foram mantidos de
propósito: a estrutura das ~30 telas continua valendo, só a pele mudou. Qual shell
usar não é declarado por tela: `app/layout.py::contexto_layout()` decide pelo
contexto da requisição.

Os gráficos do Financeiro são desenhados à mão em `<canvas>` (`static/js/painel.js`,
portado do `gestao_v18.js`) — sem biblioteca externa e sem CDN, e as cores saem das
variáveis CSS, então acompanham o tema e a marca do tenant.

A **estrutura** da vitrine veio do original (`index.html`): banner da loja,
busca fixa, navegação por categoria, cards em lista com miniatura à direita e
botão `+`, modal de produto com stepper de quantidade e sacola flutuante — hoje
vestida com a linguagem visual da página inicial, como o resto do sistema. A
diferença: lá o carrinho vivia só no navegador e o total ia junto no POST; aqui
ele continua na sessão do servidor, que é quem calcula preço — a tela só manda
ids e quantidade, então um preço adulterado no navegador não vira desconto. O
acompanhamento do pedido (`pedido_status.html`) veio com a mesma régua de etapas.

**Uma diferença de estrutura, deliberada:** no original a Gestão é UMA página com
abas. Aqui cada item do menu é uma rota, porque o bloqueio por plano é por rota —
uma página única carregaria de uma vez os recursos que o plano do tenant não
inclui. O menu usa as mesmas classes (`tab-btn`), então sai idêntico; o que muda é
que a classe veste um `<a>` em vez de um `<button>`.

A cor de destaque é a variável CSS `--brand`, sobrescrita por tenant num bloco
`<style>` do `base.html`. Como ela vai para dentro do CSS, passa
obrigatoriamente por `app/layout.py::cor_valida()`, que só aceita hex de 3 ou 6
dígitos — qualquer outro texto volta a ser a cor padrão. Sem isso, o campo de cor
seria uma porta de injeção de CSS.

O restaurante escolhe a cor, mas não escolhe se ela dá para ler. Duas variantes
saem derivadas dela, ambas garantindo 4,5:1 (WCAG AA):

- `--brand-contraste` é o texto que vai **por cima** de um preenchimento da
  marca (botão, avatar, selo de status). Uma marca amarela recebe texto quase
  preto em vez de branco.
- `--brand-texto` é a marca usada **como** texto (item ativo do menu, preço,
  código do erro). Escurece no tema claro e clareia no escuro, mexendo só na
  luminosidade — o matiz continua sendo o da marca.

Quem cuida disso é `contraste_da_marca()` e `marca_para_texto()`, em
`app/layout.py`.

A logo é gravada em `static/uploads/<slug>/`, como as fotos de produto, e trocar
a logo apaga o arquivo antigo. Sem logo, a sidebar mostra a inicial do nome.

## Cobrança da assinatura

O ciclo precisa rodar uma vez por dia — no Windows, pelo Agendador de Tarefas:

```bash
.venv\Scripts\flask ciclo-cobranca
```

Para ver o que aconteceria sem gravar nada:

```bash
.venv\Scripts\flask ciclo-cobranca --simular
```

Um tenant só passa a ser cobrado quando **`trial_termina_em` está preenchido** e
a data já passou, e quando o **plano dele tem preço maior que zero**. Tenant com
o fim do teste em branco nunca é cobrado nem suspenso — é o que protege os
tenants criados antes desta fase.

### Estoque, custo e lucro

O custo do insumo vem do **pacote de compra** (`5 kg por R$ 120`), não digitado por
unidade. A ficha técnica de cada produto diz quanto consome, e a baixa acontece
quando o pedido avança de status — gravando custo e lucro no próprio pedido,
porque o preço de compra muda com o tempo.

Saldo pode ficar negativo: significa venda sem entrada registrada. Recusar a
venda seria pior, porque o pedido já aconteceu.

**Compra de insumo não é despesa.** O custo entra pelo CMV quando o insumo é
consumido numa venda; lançar a compra também como despesa contaria o mesmo
dinheiro duas vezes. Registre compra como **entrada de estoque**.

### Como o restaurante é avisado

Não há envio de e-mail nem WhatsApp — o aviso é **dentro do próprio painel** do
restaurante. Enquanto a mensalidade está em aberto, aparece uma faixa com valor e
vencimento em todas as telas do admin; depois do vencimento ela fica vermelha e
informa quantos dias faltam para o bloqueio. Quando bloqueia, a tela explica o
motivo, o valor e há quantos dias venceu.

Defina `PLATFORM_CONTATO` no `.env` (ex.: um WhatsApp seu). Sem isso o cliente é
avisado de que deve, sem saber para quem pagar. O pagamento em si acontece por
fora: não existe PIX nem checkout da assinatura no sistema.

### O que cada plano libera

Em **Plataforma → Planos**, além do preço, cada plano marca quais recursos
inclui: painel da cozinha, salão e comanda, cupons, taxa por bairro, fotos nos
produtos, relatórios de venda, estoque com ficha técnica e financeiro. O dono do
restaurante vê a lista em **Configurações**, com o que tem e o que ganharia
mudando de plano.

Cardápio, carrinho, pedido e acompanhamento pelo cliente são a base do produto e
estão em todos os planos — não há como desligá-los.

**Plano que nunca foi configurado libera tudo.** É deliberado: sem isso, ativar
essa restrição num sistema em uso tiraria na hora todos os recursos de todos os
clientes. A restrição de um plano começa a valer no momento em que você salva a
configuração dele.

O que **não** está aqui ainda (ver [`ROADMAP.md`](ROADMAP.md) para as fases):
log de auditoria, monitoramento e backup fora do disco do servidor (Fase
10). No PIX do cliente final, a confirmação do recebimento continua sendo
feita por uma pessoa, de propósito: é o que dispensa gateway e taxa. A
lógica de negócio é portada de `C:\borbas_burguer_v17` fase por fase.

### Fotos de produto

As imagens ficam em `app/static/uploads/<slug-do-tenant>/`, fora do git. O
upload valida o **conteúdo** do arquivo com Pillow (não a extensão, que é dado
do cliente), converte para WebP e redimensiona. O limite é 5 MB por imagem
(`MAX_CONTENT_LENGTH`).

## Como rodar localmente

```bash
py -3 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env
```

Edite o `.env`: gere uma `SECRET_KEY` (`python -c "import secrets; print(secrets.token_hex(32))"`)
e defina `PLATFORM_ADMIN_PASSWORD`.

```bash
.venv\Scripts\flask db init
.venv\Scripts\flask db migrate -m "schema inicial"
.venv\Scripts\flask db upgrade
.venv\Scripts\flask seed-platform-admin
.venv\Scripts\python run.py
```

**Atenção:** o `.env` é lido **apenas** pelo comando `seed-platform-admin`, nunca
pelo login. O login confere a senha contra o hash gravado no banco. Portanto, se
você mudar `PLATFORM_ADMIN_PASSWORD` depois de já ter semeado, o login continuará
exigindo a senha antiga — rode `flask seed-platform-admin` de novo para
regravar o hash (o comando detecta o admin existente e só atualiza a senha).

O servidor sobe em `http://0.0.0.0:5000`. Como o subdomínio identifica o
tenant, acesse por `http://app.localhost:5000` (área da plataforma) ou
`http://<slug-do-tenant>.localhost:5000` (área de um tenant) — navegadores
modernos resolvem qualquer `*.localhost` para `127.0.0.1` automaticamente,
sem precisar editar o arquivo `hosts`.

## Descobrir usuários e redefinir senha

Senha de usuário existe no banco **somente como hash** — não há como recuperar,
nem para você. Quando alguém esquece, o caminho é redefinir.

Pela interface, o super-admin faz isso em **Plataforma → Tenants → Editar**: a
tela lista os usuários do restaurante e define nova senha sem pedir a antiga.
Também é lá que se ajusta plano, status da assinatura e o bloqueio manual.

Pelo terminal, quando não há navegador à mão:

Para ver quais tenants existem, o usuário de cada um e o endereço de acesso:

```bash
.venv\Scripts\flask listar-tenants
```

Para redefinir a senha de um usuário de tenant:

```bash
.venv\Scripts\flask definir-senha --tenant pizzaria-joao --usuario joao
```

A senha é pedida no terminal, com entrada oculta e confirmação. Ela **não** é
aceita como argumento de linha de comando de propósito: assim não fica no
histórico do shell nem visível na lista de processos. O filtro inclui o tenant,
então dois restaurantes podem ter um usuário `admin` sem risco de trocar a senha
do errado.

Fluxo para testar manualmente:
1. Login do super-admin em `http://app.localhost:5000/plataforma/login`.
2. Criar um tenant (isso já cria o primeiro usuário admin dele).
3. Repita para um segundo tenant.
4. Logar em `http://<slug1>.localhost:5000/login` e `http://<slug2>.localhost:5000/login`
   e confirmar que os produtos de um nunca aparecem no outro.

## Testes

```bash
.venv\Scripts\pytest
```

`tests/test_tenancy.py` é o teste mais importante: prova que não há
vazamento de dados entre tenants.
