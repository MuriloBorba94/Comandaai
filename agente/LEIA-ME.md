# Agente de Impressão — Comanda ai

Este programa faz a comanda sair na impressora da sua cozinha.

Ele precisa ficar **no computador que tem a impressora térmica ligada nele**.
Não precisa ser um computador potente, mas precisa ficar ligado enquanto o
restaurante estiver atendendo.

## Instalação, uma vez só

1. **Instale o driver da impressora** no Windows, se ainda não estiver. Teste
   pelo próprio Windows antes de continuar: se o Windows não imprime, o agente
   também não vai.

2. **Gere o código de ativação** no painel do seu restaurante, no menu
   *Impressão → Gerar código de ativação*. Ele aparece uma vez só — deixe a
   tela aberta.

3. **Descompacte esta pasta** no computador do balcão (você a baixou no painel,
   em *Impressão → Baixar o agente*) e **execute `instalar_agente.bat`** (dois
   cliques). Ele instala o que falta e já chama a configuração.

4. Na configuração, informe:
   - o **número da impressora** na lista que aparece;
   - o **endereço do seu restaurante** (o mesmo que você usa para entrar no
     painel, por exemplo `https://seurestaurante.comandaai.app.br`);
   - o **código de ativação** que você gerou no passo 2.

5. Aceite a **página de teste**. Se o papel sair, está funcionando.

6. **Execute `iniciar_agente.bat`** e deixe a janela aberta (pode minimizar).

7. **Execute `ativar_inicio_automatico.bat`** para o agente subir sozinho toda
   vez que o computador ligar. Sem isso, alguém precisa lembrar de abrir o
   agente todo dia — e vai esquecer justo no sábado.

Confira no painel: a tela de Impressão passa a mostrar **Conectado**, com o
nome deste computador e o da impressora.

## O dia a dia

- O agente **procura** o servidor a cada 3 segundos. Sua internet não recebe
  conexão de fora, e você não precisa de IP fixo nem de mexer no roteador.
- Se a internet cair, ele fica tentando sozinho. Quando voltar, imprime o que
  ficou na fila — nada se perde.
- Se a impressora ficar sem papel, a comanda aparece como **Falhou** no painel
  e o agente tenta de novo. Depois de cinco tentativas ele para; resolva o
  papel e use **Imprimir** no painel da cozinha.
- Comanda não sai duas vezes. Mesmo se a internet cair bem no meio, o agente
  anota o que já imprimiu (`impressoes_confirmadas.json`) e não repete.

## Se algo der errado

O arquivo `agente.log`, nesta pasta, tem tudo o que aconteceu com data e hora.
É o primeiro lugar para olhar, e o que mandar para o suporte.

| O que aparece | O que é |
|---|---|
| `Código de ativação inválido ou revogado` | O código foi gerado de novo no painel. Rode `configurar_agente.bat` e cole o novo. |
| `Sem conexão com o servidor` | A internet do restaurante caiu, ou o endereço está errado. |
| `pywin32 não está instalado` | Rode `instalar_agente.bat` de novo. |
| Nada acontece e o painel diz **Desconectado** | O `iniciar_agente.bat` não está rodando. |

## Arquivos que aparecem sozinhos

`agente_config.json`, `impressoes_confirmadas.json` e `agente.log` são criados
pelo programa e ficam só neste computador.

**Não compartilhe o `agente_config.json`**: ele contém o código de ativação, e
quem tiver esse código consegue ler as comandas do seu restaurante. Se ele
vazar, gere um código novo no painel — o antigo para de valer na hora.
