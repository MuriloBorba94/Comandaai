# Cobrar as mensalidades automaticamente (Asaas)

Hoje a mensalidade que cada restaurante paga a você funciona no **modo manual**:
o sistema emite a cobrança, você recebe o PIX e marca como paga em
*Plataforma → Cobranças*. Funciona, e continua funcionando — não há pressa para
sair daqui.

Com o Asaas ligado, muda uma coisa só, e é a que importa: **o acesso do
restaurante volta sozinho quando ele paga**. Você deixa de ser o intermediário
entre o pagamento e a liberação.

> **Faça tudo no sandbox primeiro.** É uma conta de testes gratuita e completa:
> emite fatura de verdade, aceita pagamento fictício e dispara o webhook. Dá
> para exercitar o fluxo inteiro — inclusive suspender e liberar um restaurante
> — sem cobrar ninguém e sem mexer em dinheiro real.

---

## 1. Criar a conta e pegar a chave

1. Crie a conta no **sandbox** em `https://sandbox.asaas.com`.
2. Lá dentro: **Integrações → Chave de API → Gerar chave**.
3. Copie a chave. Ela começa com `$aact_`.

---

## 2. Inventar o segredo do webhook

O webhook é o endereço que o Asaas chama para dizer "fulano pagou". Ele fica
público na internet, então precisa de um segredo — senão qualquer um poderia
marcar mensalidade como paga.

**Na sua máquina**, gere um:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Guarde o resultado: ele vai nos passos 3 e 4, **igual nos dois**.

---

## 3. Configurar o servidor

**No servidor:**

```bash
sudo -u comandaai nano /opt/comandaai/.env
```

Acrescente no fim (usando a chave do passo 1 e o segredo do passo 2):

```
ASAAS_AMBIENTE=sandbox
ASAAS_API_KEY=cole-a-chave-aqui
ASAAS_WEBHOOK_TOKEN=cole-o-segredo-aqui
```

Salve (`Ctrl+O`, `Enter`, `Ctrl+X`) e reinicie:

```bash
sudo systemctl restart comandaai
```

---

## 4. Cadastrar o webhook no Asaas

No painel do Asaas: **Integrações → Webhooks → Adicionar**.

| Campo | O que preencher |
|---|---|
| URL | `https://app.comandaai.app.br/webhooks/asaas` |
| Email | o seu, para o Asaas avisar se o webhook começar a falhar |
| Token de autenticação | **o mesmo segredo do passo 2** |
| Versão da API | v3 |
| Eventos | os de **Cobranças** (`PAYMENT_*`) |

> É o `app.` do começo, e não o subdomínio de um restaurante: quem paga é o
> restaurante, mas a conta é da plataforma.

---

## 5. Ligar um restaurante por vez

O provedor é escolhido **por restaurante**, de propósito: virar a chave de todo
mundo de uma vez é como se descobre no dia seguinte que faltava o CNPJ de
metade deles.

O Asaas exige **CPF ou CNPJ** para criar o cliente. Confira que o restaurante
tem um cadastrado em *Plataforma → Restaurantes* antes de ligar.

Para ligar, defina `assinatura_provider` como `asaas` naquele restaurante.

---

## 6. Conferir que funcionou

Force a emissão da mensalidade:

```bash
cd /opt/comandaai && sudo -u comandaai env FLASK_APP=run.py .venv/bin/python -m flask ciclo-cobranca
```

Em *Plataforma → Cobranças*, a cobrança daquele restaurante deve aparecer com
link de pagamento. Abra o link, pague com os dados fictícios do sandbox, e
observe: em segundos a cobrança vira **paga** sozinha.

Se o restaurante estava suspenso, ele volta ao ar na hora.

---

## Quando algo não sai como esperado

**A cobrança aparece sem link de pagamento.** O Asaas estava fora do ar, a
chave está errada ou falta o CNPJ do restaurante. O motivo fica escrito na
observação da própria cobrança. Emitir a mensalidade **nunca** falha por causa
do gateway — mês sem cobrança não bloqueia ninguém, só some com a sua receita
em silêncio. Depois de corrigir:

```bash
cd /opt/comandaai && sudo -u comandaai env FLASK_APP=run.py .venv/bin/python -m flask reemitir-no-gateway
```

Use `--simular` antes para ver o que ele faria.

**Paguei no sandbox e a cobrança não virou paga.** O webhook não chegou. No
painel do Asaas, em Webhooks, há o histórico de envios com a resposta de cada
um. Resposta `401` significa token diferente entre o `.env` e o painel.

```bash
sudo journalctl -u comandaai -n 50 --no-pager | grep -i asaas
```

**Um pagamento foi estornado.** O sistema registra na observação e **não**
bloqueia o restaurante sozinho. Derrubar a loja de alguém por causa de um
webhook é um martelo grande demais para uma decisão que sempre tem contexto
humano atrás — cancele a liberação na mão, se for o caso.

---

## Passar para produção

Quando o fluxo inteiro tiver funcionado no sandbox: crie a conta real, gere a
chave real, troque as três linhas do `.env` (`ASAAS_AMBIENTE=producao`) e
cadastre o webhook de novo no painel de produção. O sandbox e a produção são
contas separadas e não compartilham nada.

O padrão do sistema é `sandbox` justamente para isto: uma configuração pela
metade não sai cobrando ninguém de verdade.
