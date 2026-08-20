"""Agente de impressão do Comanda ai.

Roda no computador do restaurante, o que tem a impressora térmica ligada nele.
De tempos em tempos ele PERGUNTA ao servidor se há comanda para imprimir. Nunca
o contrário — é isso que dispensa IP fixo, porta aberta no roteador e qualquer
conversa com a operadora de internet.

O agente não sabe formatar comanda: ele recebe o texto pronto do servidor e
manda para a impressora. Assim, corrigir uma linha torta é mexer no servidor,
não sair atualizando programa em cada restaurante.

Sem dependência externa além do pywin32 (que é o que fala com a impressora do
Windows): só a biblioteca padrão do Python.
"""

from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "agente_config.json"
JOURNAL_FILE = BASE_DIR / "impressoes_confirmadas.json"
LOG_FILE = BASE_DIR / "agente.log"
VERSAO = "1.0"


class ErroDoServidor(RuntimeError):
    def __init__(self, mensagem: str, codigo: int | None = None):
        super().__init__(mensagem)
        self.codigo = codigo


def log(mensagem: str) -> None:
    linha = f"[{datetime.now():%d/%m/%Y %H:%M:%S}] {mensagem}"
    print(linha, flush=True)
    try:
        with LOG_FILE.open("a", encoding="utf-8") as saida:
            saida.write(linha + "\n")
    except OSError:
        # Log é conveniência; se o disco recusar, imprimir a comanda importa mais.
        pass


def carregar_config() -> dict:
    if not CONFIG_FILE.exists():
        raise RuntimeError("Agente ainda não configurado. Execute configurar_agente.bat.")
    config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    if any(not config.get(chave) for chave in ("server_url", "token", "printer_name")):
        raise RuntimeError("Configuração incompleta. Execute configurar_agente.bat de novo.")
    return config


def post(config: dict, caminho: str, payload: dict) -> dict:
    requisicao = urllib.request.Request(
        f"{config['server_url'].rstrip('/')}{caminho}",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {config['token']}",
            "Content-Type": "application/json",
            "User-Agent": f"ComandaAiPrintAgent/{VERSAO}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(requisicao, timeout=25) as resposta:
            return json.loads(resposta.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            mensagem = json.loads(exc.read().decode("utf-8")).get("mensagem")
        except Exception:
            mensagem = None
        raise ErroDoServidor(mensagem or f"O servidor respondeu com erro {exc.code}.", exc.code) from exc
    except urllib.error.URLError as exc:
        raise ErroDoServidor(f"Sem conexão com o servidor: {exc.reason}") from exc


def imprimir(impressora: str, nome_do_trabalho: str, conteudo: str, codificacao: str) -> None:
    try:
        import win32print
    except ImportError as exc:
        raise RuntimeError("pywin32 não está instalado. Execute instalar_agente.bat de novo.") from exc

    handle = win32print.OpenPrinter(impressora)
    try:
        win32print.StartDocPrinter(handle, 1, (nome_do_trabalho, None, "RAW"))
        try:
            win32print.StartPagePrinter(handle)
            # ESC @ zera a impressora, GS V 0 corta o papel no fim. São os dois
            # comandos que toda térmica de balcão entende.
            dados = b"\x1b\x40" + conteudo.encode(codificacao, errors="replace") + b"\n\n\x1d\x56\x00"
            win32print.WritePrinter(handle, dados)
            win32print.EndPagePrinter(handle)
        finally:
            win32print.EndDocPrinter(handle)
    finally:
        win32print.ClosePrinter(handle)


def ler_diario() -> list[int]:
    """Ids de trabalhos que já saíram no papel neste computador.

    É a proteção contra comanda repetida: se a internet cair DEPOIS de imprimir
    e ANTES de confirmar, o servidor vai oferecer o mesmo trabalho de novo — e
    o cozinheiro receberia o pedido duas vezes. Com o diário, o agente confirma
    sem imprimir outra vez.
    """
    try:
        valores = json.loads(JOURNAL_FILE.read_text(encoding="utf-8"))
        return [int(v) for v in valores][-500:] if isinstance(valores, list) else []
    except (FileNotFoundError, json.JSONDecodeError, TypeError, ValueError):
        return []


def anotar_no_diario(job_id: int) -> None:
    valores = ler_diario()
    if job_id not in valores:
        valores.append(job_id)
    JOURNAL_FILE.write_text(json.dumps(valores[-500:]), encoding="utf-8")


def identificacao(config: dict) -> dict:
    return {
        "agent_name": config.get("agent_name") or socket.gethostname(),
        "printer_name": config["printer_name"],
        "version": VERSAO,
    }


def confirmar(config: dict, trabalho: dict, ok: bool, erro: str = "") -> None:
    post(
        config,
        "/api/impressao/agente/resultado",
        {
            "job_id": trabalho["job_id"],
            "claim_token": trabalho["claim_token"],
            "ok": ok,
            "error": erro[:500],
        },
    )


def main() -> int:
    try:
        config = carregar_config()
    except Exception as exc:
        print(exc)
        return 1

    intervalo = max(2, int(config.get("poll_seconds", 3)))
    log(f"Agente {VERSAO} iniciado. Impressora: {config['printer_name']}")
    log("Pode deixar esta janela minimizada. Fechá-la para o agente.")
    conectado = False

    while True:
        try:
            resposta = post(config, "/api/impressao/agente/proximo", identificacao(config))
            if not conectado:
                log("Conectado ao servidor. Aguardando comandas.")
                conectado = True

            trabalho = resposta.get("trabalho")
            if not trabalho:
                time.sleep(intervalo)
                continue

            job_id = int(trabalho["job_id"])
            if job_id in ler_diario():
                log(f"{trabalho.get('job_name')} já foi impresso; confirmando sem repetir o papel.")
                confirmar(config, trabalho, True)
                continue

            try:
                imprimir(
                    config["printer_name"],
                    trabalho.get("job_name") or f"Comanda {job_id}",
                    trabalho["content"],
                    trabalho.get("encoding") or config.get("encoding", "cp850"),
                )
            except Exception as erro_impressao:
                log(f"Falha ao imprimir {trabalho.get('job_name')}: {erro_impressao}")
                try:
                    confirmar(config, trabalho, False, str(erro_impressao))
                except Exception as erro_aviso:
                    log(f"Não foi possível avisar o servidor da falha: {erro_aviso}")
                # Pausa antes de tentar de novo: quase sempre é papel acabando
                # ou impressora desligada, e insistir de imediato não resolve.
                time.sleep(15)
                continue

            # A anotação vem ANTES da confirmação, de propósito: se a conexão
            # cair exatamente aqui, o diário já sabe que o papel saiu.
            anotar_no_diario(job_id)
            log(f"{trabalho.get('job_name')} impresso.")
            confirmar(config, trabalho, True)

        except KeyboardInterrupt:
            log("Agente encerrado.")
            return 0
        except ErroDoServidor as exc:
            # Repetir a mesma mensagem a cada 3 segundos entupiria o log e
            # esconderia o que interessa; só o primeiro erro da sequência sai.
            if conectado or exc.codigo == 401:
                log(str(exc))
            conectado = False
            time.sleep(15 if exc.codigo in {401, 409} else 5)
        except Exception as exc:
            log(f"Erro inesperado: {exc}")
            conectado = False
            time.sleep(5)


if __name__ == "__main__":
    raise SystemExit(main())
