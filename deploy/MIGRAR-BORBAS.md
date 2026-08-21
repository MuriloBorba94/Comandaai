# Migrar o Borba's Burguer para o Comanda ai

Passo a passo para transformar o sistema antigo (`C:\borbas_burguer_v17`) no
primeiro restaurante do Comanda ai, em `borbas.comandaai.app.br`.

**Nada é destrutivo.** O sistema antigo continua funcionando o tempo todo, e a
importação pode ser refeita quantas vezes você quiser.

O que atravessa: loja, cardápio com fotos, adicionais, bairros, cupons, insumos,
fichas técnicas e usuários **com a senha de sempre**. O que fica para trás: o
histórico de pedidos — o porquê está no fim deste guia.

Você vai alternar entre duas janelas. Preste atenção no início da linha:

- `PS C:\...>` → **sua máquina** (PowerShell)
- `root@vps...#` → **o servidor** (SSH)

---

## 0. Atualizar o servidor

**No servidor.** O importador é código novo: se o servidor ainda não o baixou, o
comando do passo 5 responde `Usage: python -m flask ...`, que é o jeito do Click
dizer "esse comando não existe".

```bash
sudo -u comandaai git -C /opt/comandaai pull --ff-only
```

Puxe **como `comandaai`**, não como root: o repositório pertence àquele usuário e
o git recusa operar num repositório de outro dono (`detected dubious ownership`).

```bash
sudo systemctl restart comandaai
```

Confirme que os comandos chegaram:

```bash
cd /opt/comandaai && sudo -u comandaai env FLASK_APP=run.py .venv/bin/python -m flask --help
```

A lista tem que incluir `importar-legado` e `remover-tenant`. Se não incluir, o
`pull` não trouxe nada — confira se ele disse `Already up to date` (o que
significa que o código novo ainda não foi publicado) ou se deu erro.

---

## 1. Gerar uma cópia limpa do banco antigo

**Na sua máquina.**

O sistema antigo pode estar rodando, e copiar o arquivo com o Explorer sai
corrompido — SQLite guarda escrita pendente num arquivo separado (`.db-wal`).
Este comando faz a cópia do jeito certo:

```powershell
cd C:\borbas_saas_v1
```

```powershell
.venv\Scripts\python.exe -c "import os, sqlite3; destino=os.path.join(os.path.expanduser('~'),'legado.db'); o=sqlite3.connect('file:C:/borbas_burguer_v17/instance/hamburgueria.db?mode=ro', uri=True); d=sqlite3.connect(destino); o.backup(d); d.close(); o.close(); print('copia criada em', destino)"
```

Duas coisas que esse comando resolve e não são óbvias:

- **Não use `sqlite3` direto**: ele não vem instalado no Windows. O comando usa o
  Python que já está no projeto.
- **O destino é a sua pasta de usuário**, não `C:\`. Escrever na raiz do disco
  exige rodar como administrador.

Ele imprime o caminho da cópia. Confirme que ela está íntegra:

```powershell
.venv\Scripts\python.exe -c "import os, sqlite3; c=sqlite3.connect(os.path.join(os.path.expanduser('~'),'legado.db')); print('integridade:', c.execute('pragma integrity_check').fetchone()[0]); print('produtos:', c.execute('select count(*) from produto').fetchone()[0])"
```

Tem que responder `integridade: ok` e `produtos: 30`.

---

## 2. Mandar o banco e as fotos para o servidor

**Na sua máquina.** Troque `SEU_IP` pelo IP da VPS. Ele vai pedir a senha do root.

As aspas são obrigatórias: o caminho da sua pasta de usuário tem espaço
("Murilo Borba"), e sem elas o `scp` entende como se fossem dois arquivos.

```powershell
scp "$HOME\legado.db" root@SEU_IP:/tmp/
```

```powershell
scp -r "C:\borbas_burguer_v17\app\static\uploads" root@SEU_IP:/tmp/fotos_legado
```

O segundo demora um pouco: são 37 arquivos de imagem.

---

## 3. Liberar leitura no servidor

**No servidor.** Os arquivos chegaram como root, e quem vai lê-los é o usuário
`comandaai`:

```bash
chmod -R a+rX /tmp/legado.db /tmp/fotos_legado
```

Confira que chegou tudo:

```bash
ls -l /tmp/legado.db && ls /tmp/fotos_legado | wc -l
```

O primeiro mostra o arquivo; o segundo tem que responder `37`.

---

## 4. Conferir o plano

**No servidor.** O restaurante entra num plano, e ele precisa existir:

```bash
cd /opt/comandaai && sudo -u comandaai env FLASK_APP=run.py .venv/bin/python -m flask seed-planos
```

Se os planos já existirem, ele avisa e não duplica nada. Use `pro` no passo
seguinte, ou o slug que você preferir.

---

## 5. SIMULAR a importação

**No servidor.** Este comando roda a importação inteira e **desfaz no fim**. É
para você ver o relatório sem gravar nada.

```bash
cd /opt/comandaai && sudo -u comandaai env FLASK_APP=run.py .venv/bin/python -m flask importar-legado --banco /tmp/legado.db --fotos /tmp/fotos_legado --slug borbas --email contato@borbasburguer.com.br --mesas 10 --plano pro --simular
```

Ajuste antes de rodar:

| Opção | O que é |
|---|---|
| `--slug borbas` | Vira o endereço: `borbas.comandaai.app.br` |
| `--email` | E-mail de contato do restaurante |
| `--mesas 10` | Quantas mesas tem o salão. O sistema antigo não guardava isso |
| `--plano pro` | Plano do restaurante |

O relatório esperado, com os seus dados:

```
SIMULACAO — nada foi gravado: borbas
  restaurante................. 1
  categorias.................. 4
  produtos.................... 30
  adicionais.................. 10
  bairros..................... 5
  cupons...................... 1
  insumos..................... 14
  linhas de ficha técnica..... 56
  usuários.................... 3
```

**Confira os números antes de seguir.** Se algum estiver zerado ou muito
diferente, pare e me mande o relatório.

---

## 6. Importar de verdade

**No servidor.** É o mesmo comando **sem** `--simular`:

```bash
cd /opt/comandaai && sudo -u comandaai env FLASK_APP=run.py .venv/bin/python -m flask importar-legado --banco /tmp/legado.db --fotos /tmp/fotos_legado --slug borbas --email contato@borbasburguer.com.br --mesas 10 --plano pro
```

Agora o relatório traz uma linha a mais, `fotos copiadas 30`, e termina com
`Pronto`.

---

## 7. Conferir

**No servidor**, veja se o restaurante está lá:

```bash
cd /opt/comandaai && sudo -u comandaai env FLASK_APP=run.py .venv/bin/python -m flask listar-tenants
```

**No navegador**, abra:

- `https://borbas.comandaai.app.br` → o cardápio, com as fotos
- `https://borbas.comandaai.app.br/login` → entre com o usuário **Murilo** e a
  **sua senha de sempre**

Não precisa mexer em DNS nem em certificado: o curinga `*.comandaai.app.br` já
cobre qualquer restaurante novo.

Depois de entrar, vale olhar:

- **Produtos** — as 30 fotos apareceram?
- **Custos** — a coluna "Status" aponta o que está abaixo do preço sugerido pela
  sua meta de 40%
- **Estoque** — os 14 insumos com o saldo que estava no sistema antigo

---

## 8. Se algo estiver errado: refazer

A importação recusa importar por cima de um restaurante que já existe, porque
isso duplicaria o cardápio inteiro. Para refazer, remova e importe de novo:

```bash
cd /opt/comandaai && sudo -u comandaai env FLASK_APP=run.py .venv/bin/python -m flask remover-tenant --slug borbas --apagar-fotos
```

Ele mostra o que vai sumir e pede que você **digite `borbas`** para confirmar —
confirmação de uma tecla é o que faz alguém apagar o restaurante errado no
piloto automático.

Depois é só repetir o passo 6.

> Enquanto o restaurante não estiver recebendo pedido de verdade, refazer não
> custa nada. Depois que começar a vender, remover apaga o histórico junto.

---

## 9. Limpar o servidor

**No servidor**, depois que estiver tudo certo. O banco antigo tem dados de
clientes e não deve ficar largado em `/tmp`:

```bash
rm -rf /tmp/legado.db /tmp/fotos_legado
```

E na sua máquina:

```powershell
del "$HOME\legado.db"
```

---

## 10. Rodar em paralelo

Não desligue o sistema antigo ainda. A recomendação é:

1. **Uma semana** usando os dois: pedido novo entra pelo Comanda ai, o antigo
   fica de reserva e para consulta do histórico.
2. Quando a equipe estiver confortável e você tiver conferido um fechamento de
   caixa completo, aponte o link do cardápio (Instagram, WhatsApp) para o
   endereço novo.
3. Só então desligue o antigo.

Para a **impressão na cozinha** funcionar como no sistema antigo, instale o
agente no computador do balcão: painel > menu **Impressão**, onde ficam o
*Gerar código de ativação* e o *Baixar o agente (.zip)*. O passo a passo vai
dentro do próprio pacote, em `LEIA-ME.md`.

Para **receber PIX pelo site** como no sistema antigo, cadastre a chave em
*Loja e identidade → Receber PIX pelo site*. A tela mostra uma prévia de como o
seu nome vai aparecer no aplicativo do banco do cliente — confira ali, porque o
padrão do Banco Central corta em 25 caracteres e tira os acentos.

A chave que está no sistema antigo é `murilo-borba@jim.com`, com recebedor
"Borbas Burguer" e cidade "Vicência".

O que ainda falta para não sentir falta do sistema antigo:

- **WhatsApp** (Fase 7)

---

## Por que o histórico de pedidos não vem

No sistema antigo, os itens de um pedido são um texto solto no registro
(`"2x X-Tudo\n1x Refri"`), não linhas de uma tabela. Importar os 1101 pedidos
criaria mil registros sem item, sem custo e sem lucro — que é exatamente o que
alimenta o CMV, o "mais vendidos" e a margem na tela de Financeiro.

O resultado seria um financeiro cheio de números errados, parecendo certo. Começar
limpo é mais honesto: o histórico continua no sistema antigo, disponível para
consulta, e o Comanda ai passa a contar a verdade a partir do primeiro pedido.

Se você preferir ter o histórico mesmo assim, dá para fazer — mas esses pedidos
precisam entrar marcados de um jeito que o financeiro não os conte como se o
custo fosse conhecido. Me diga que eu faço.
