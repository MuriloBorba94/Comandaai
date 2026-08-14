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
- **Cobrança da assinatura** (Fase 4, núcleo): catálogo de planos com preço,
  mensalidade por tenant, ciclo que suspende quem passa da carência e libera ao
  registrar o pagamento. Provedor `manual` (você recebe o PIX e marca como pago);
  a integração com gateway fica para quando houver chave de API.
- **Relatórios de venda** e **estoque com ficha técnica** (Fase 9): custo e lucro
  por pedido, baixa automática de insumo ao vender, despesas a pagar e resultado
  do período.
- **Layout do Borba's Burguer portado**: painel claro/escuro com sidebar e
  commandbar, vitrine escura para o cliente final, e identidade por tenant —
  cada restaurante envia a sua logo e escolhe a cor da marca em
  `/admin/configuracoes`, sem interferir no vizinho.
- Migrations versionadas com Flask-Migrate/Alembic desde o início.

## Identidade visual de cada tenant

O design system inteiro vive em `app/static/css/comanda.css`, com dois ambientes:
o **painel** (admin do restaurante e área da plataforma), claro por padrão e com
alternador para escuro guardado no `localStorage` de quem opera; e a **vitrine**
(o cardápio público), sempre escura. Qual dos dois usar não é declarado por tela:
`app/layout.py::contexto_layout()` decide pelo contexto da requisição.

A cor de destaque é a variável CSS `--brand`, sobrescrita por tenant num bloco
`<style>` do `base.html`. Como ela vai para dentro do CSS, passa
obrigatoriamente por `app/layout.py::cor_valida()`, que só aceita hex de 3 ou 6
dígitos — qualquer outro texto volta a ser a cor padrão. Sem isso, o campo de cor
seria uma porta de injeção de CSS.

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
PIX no pedido, WhatsApp, agente de impressão, integração com gateway de cobrança
e deploy de produção. A lógica de negócio é portada de
`C:\borbas_burguer_v17` fase por fase.

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
