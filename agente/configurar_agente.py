"""Configurador do agente de impressão do Comanda ai.

Faz três perguntas — impressora, endereço do restaurante e código de ativação —
e, antes de dar por concluído, PROVA que as duas pontas funcionam: fala com o
servidor e imprime uma página de teste. Um configurador que só grava um arquivo
e diz "pronto" empurra a descoberta do erro para a noite de sábado.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "agente_config.json"
VERSAO = "1.0"
LARGURA = 42


def carregar_win32print():
    try:
        import win32print

        return win32print
    except ImportError as exc:
        raise RuntimeError("O componente pywin32 não está instalado. Execute instalar_agente.bat.") from exc


def impressoras() -> tuple[list[str], str]:
    win32print = carregar_win32print()
    sinalizadores = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
    nomes = sorted({linha[2] for linha in win32print.EnumPrinters(sinalizadores) if linha[2]}, key=str.casefold)
    try:
        padrao = win32print.GetDefaultPrinter() or ""
    except Exception:
        padrao = ""
    return nomes, padrao


def imprimir(impressora: str, conteudo: str, codificacao: str = "cp850") -> None:
    win32print = carregar_win32print()
    handle = win32print.OpenPrinter(impressora)
    try:
        win32print.StartDocPrinter(handle, 1, ("Comanda ai - teste", None, "RAW"))
        try:
            win32print.StartPagePrinter(handle)
            dados = b"\x1b\x40" + conteudo.encode(codificacao, errors="replace") + b"\n\n\x1d\x56\x00"
            win32print.WritePrinter(handle, dados)
            win32print.EndPagePrinter(handle)
        finally:
            win32print.EndDocPrinter(handle)
    finally:
        win32print.ClosePrinter(handle)


def post(servidor: str, token: str, caminho: str, payload: dict) -> dict:
    requisicao = urllib.request.Request(
        f"{servidor.rstrip('/')}{caminho}",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": f"ComandaAiPrintAgent/{VERSAO}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(requisicao, timeout=20) as resposta:
            return json.loads(resposta.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            mensagem = json.loads(exc.read().decode("utf-8")).get("mensagem")
        except Exception:
            mensagem = None
        raise RuntimeError(mensagem or f"O servidor respondeu com erro {exc.code}.") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Não foi possível falar com {servidor}: {exc.reason}") from exc


def escolher_impressora() -> str | None:
    lista, padrao = impressoras()
    if not lista:
        print("Nenhuma impressora foi encontrada neste computador.")
        print("Instale o driver da impressora térmica no Windows e rode este configurador de novo.")
        return None

    print("\nImpressoras instaladas neste computador:\n")
    for indice, nome in enumerate(lista, 1):
        marca = "  (padrão do Windows)" if nome == padrao else ""
        print(f"  {indice}. {nome}{marca}")

    while True:
        valor = input("\nDigite o número da impressora térmica: ").strip()
        try:
            return lista[int(valor) - 1]
        except (ValueError, IndexError):
            print("Opção inválida. Digite um dos números da lista.")


def perguntar_endereco() -> str | None:
    print("\nO endereço é o mesmo que você usa para entrar no painel,")
    print("por exemplo: https://seurestaurante.comandaai.app.br")
    endereco = input("\nEndereço do seu restaurante: ").strip().rstrip("/")
    if not endereco:
        print("O endereço é obrigatório.")
        return None
    if not endereco.startswith(("https://", "http://")):
        # Erro de digitação mais comum: colar só o domínio. Corrigir em silêncio
        # é melhor do que devolver a pessoa ao começo por causa de 8 caracteres.
        endereco = "https://" + endereco
        print(f"Entendido como: {endereco}")
    return endereco


def main() -> int:
    print("\n" + "=" * 46)
    print("  COMANDA AI - CONFIGURACAO DO AGENTE".center(46))
    print("=" * 46)

    impressora = escolher_impressora()
    if impressora is None:
        return 1

    endereco = perguntar_endereco()
    if endereco is None:
        return 1

    print("\nO código de ativação está no painel do restaurante,")
    print("no menu Impressão > Gerar código de ativação.")
    token = input("\nCole o código de ativação: ").strip()
    if len(token) < 20:
        print("Esse código parece curto demais. Copie o código inteiro do painel.")
        return 1

    computador = input(f"\nNome deste computador [{socket.gethostname()}]: ").strip() or socket.gethostname()

    print("\nFalando com o servidor...")
    try:
        resposta = post(
            endereco,
            token,
            "/api/impressao/agente/ping",
            {"agent_name": computador, "printer_name": impressora, "version": VERSAO},
        )
    except Exception as exc:
        # Nada é gravado quando a conexão falha: um arquivo com código errado
        # faria o agente girar em erro sem ninguém entender o motivo.
        print(f"\nNão deu certo: {exc}")
        print("\nConfira o endereço e se o código foi copiado inteiro, e rode de novo.")
        return 1

    restaurante = resposta.get("restaurante") or "seu restaurante"
    print(f"Conectado a {restaurante}.")

    CONFIG_FILE.write_text(
        json.dumps(
            {
                "server_url": endereco,
                "token": token,
                "printer_name": impressora,
                "agent_name": computador,
                "encoding": "cp850",
                "poll_seconds": 3,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"Configuração salva em {CONFIG_FILE.name}.")

    resposta_teste = input("\nImprimir uma página de teste agora? [S/n]: ").strip().lower()
    if resposta_teste not in {"n", "nao", "não"}:
        conteudo = "\n".join(
            [
                "COMANDA AI".center(LARGURA),
                "AGENTE DE IMPRESSAO".center(LARGURA),
                "-" * LARGURA,
                f"Restaurante: {restaurante}",
                f"Computador.: {computador}",
                f"Impressora.: {impressora}",
                "-" * LARGURA,
                "Conexao e impressora configuradas.",
                "",
                "",
            ]
        )
        try:
            imprimir(impressora, conteudo)
            print("Página de teste enviada para a impressora.")
        except Exception as exc:
            print(f"\nO servidor conectou, mas a impressão falhou: {exc}")
            print("A configuração está salva. Confira se a impressora está ligada e com papel.")
            return 1

    print("\nPronto. Agora execute iniciar_agente.bat e deixe a janela aberta.")
    print("Para o agente subir sozinho com o Windows, execute ativar_inicio_automatico.bat.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
