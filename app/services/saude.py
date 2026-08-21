"""Está tudo de pé? — a pergunta que um monitor externo faz de minuto em minuto.

Duas respostas diferentes para dois públicos, e a diferença importa:

- **Pública** (`/saude`): só "ok" ou "não ok", com o código HTTP. É o que um
  serviço de monitoramento gratuito precisa, e é tudo o que ele deve saber. Uma
  resposta detalhada num endereço aberto conta a estranhos qual é o banco, quão
  cheio está o disco e quantos clientes existem — informação que ajuda quem
  quer atacar e não ajuda mais ninguém.
- **Detalhada** (área da plataforma): o quadro inteiro, para você.

O que é considerado grave (derruba a resposta para 503) é curto de propósito:
banco inacessível e migration pendente. Fila de impressão parada ou disco em
80% são avisos — coisas para olhar, não para acordar alguém de madrugada. Um
alarme que dispara por qualquer coisa é um alarme que as pessoas aprendem a
ignorar.
"""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from flask import current_app
from sqlalchemy import text

from ..extensions import db

# Acima disso o disco vira aviso. Num servidor com SQLite, disco cheio não
# deixa nem gravar pedido — mas 85% ainda dá muito tempo para agir.
DISCO_ALERTA = 85

# Idade máxima esperada do backup mais recente. O agendamento é diário, então
# 30h cobre um dia de folga sem acusar problema que não existe.
BACKUP_ALERTA_HORAS = 30


def _banco() -> dict:
    try:
        db.session.execute(text("select 1"))
        return {"ok": True, "detalhe": "responde"}
    except Exception as exc:  # noqa: BLE001 - qualquer falha aqui é grave
        return {"ok": False, "detalhe": f"não responde: {exc}"[:200]}


def _migrations() -> dict:
    """A versão do banco bate com a do código?

    Este é o estado que aparece depois de uma publicação pela metade: código
    novo rodando contra banco velho. Ele não dá erro em toda página — dá erro na
    primeira tela que usa a coluna nova, que pode ser só na hora do jantar.
    """
    try:
        from alembic.migration import MigrationContext
        from alembic.script import ScriptDirectory
        from flask_migrate import current as _  # noqa: F401  (garante o app carregado)

        with db.engine.connect() as conexao:
            atual = MigrationContext.configure(conexao).get_current_revision()

        diretorio = ScriptDirectory(str(Path(current_app.root_path).parent / "migrations"))
        topo = diretorio.get_current_head()

        if atual == topo:
            return {"ok": True, "detalhe": f"na versão {atual}"}

        if atual is None:
            # Banco sem carimbo nenhum: foi criado por `create_all()` e não pelas
            # migrations — é o caso de um banco de teste ou de uma instalação
            # feita à mão. Não é a mesma coisa que "está atrasado", e tratar
            # como grave faria a checagem gritar em toda suíte de teste.
            return {
                "ok": False,
                "aviso": True,
                "detalhe": "o banco não foi criado pelas migrations (sem versão registrada)",
            }

        return {
            "ok": False,
            "detalhe": f"o banco está em {atual} e o código espera {topo}. Rode: flask db upgrade",
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detalhe": f"não foi possível conferir: {exc}"[:200]}


def _disco() -> dict:
    try:
        uso = shutil.disk_usage(current_app.instance_path)
        por_cento = round(uso.used / uso.total * 100)
        livre_gb = uso.free / (1024**3)
        return {
            "ok": por_cento < DISCO_ALERTA,
            "detalhe": f"{por_cento}% usado, {livre_gb:.1f} GB livres",
            "aviso": True,
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": True, "detalhe": f"não foi possível medir: {exc}"[:120], "aviso": True}


def _backup() -> dict:
    from .backup import mais_recente

    pasta = Path(current_app.root_path).parent / "backups"
    arquivo = mais_recente(pasta)
    if arquivo is None:
        return {"ok": False, "detalhe": "nenhum backup encontrado", "aviso": True}

    horas = (datetime.now().timestamp() - arquivo.stat().st_mtime) / 3600
    return {
        "ok": horas <= BACKUP_ALERTA_HORAS,
        "detalhe": f"o mais recente tem {horas:.0f} h ({arquivo.name})",
        "aviso": True,
    }


def _fila_de_impressao() -> dict:
    from ..models.impressao import STATUS_ERRO, ImpressaoJob

    try:
        com_erro = ImpressaoJob.query.filter_by(status=STATUS_ERRO).count()
        return {
            "ok": com_erro == 0,
            "detalhe": "sem falhas" if not com_erro else f"{com_erro} comanda(s) não saíram",
            "aviso": True,
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": True, "detalhe": f"não foi possível conferir: {exc}"[:120], "aviso": True}


def _avisos_pendentes() -> dict:
    from ..models.notificacao import STATUS_ERRO, Notificacao

    try:
        com_erro = Notificacao.query.filter_by(status=STATUS_ERRO).count()
        return {
            "ok": com_erro == 0,
            "detalhe": "sem falhas" if not com_erro else f"{com_erro} aviso(s) não saíram",
            "aviso": True,
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": True, "detalhe": f"não foi possível conferir: {exc}"[:120], "aviso": True}


VERIFICACOES = {
    "banco": _banco,
    "migrations": _migrations,
    "disco": _disco,
    "backup": _backup,
    "impressao": _fila_de_impressao,
    "avisos": _avisos_pendentes,
}


def checar() -> dict:
    """Roda tudo e devolve o quadro completo.

    `grave` é o que derruba a resposta pública para 503. Uma verificação
    marcada com `aviso` nunca é grave: ela conta algo para olhar, não algo que
    justifique acordar alguém.
    """
    resultados = {}
    grave = False
    for nome, funcao in VERIFICACOES.items():
        try:
            resultado = funcao()
        except Exception as exc:  # noqa: BLE001 - a checagem não pode derrubar a checagem
            resultado = {"ok": False, "detalhe": f"erro na verificação: {exc}"[:200], "aviso": True}
        resultados[nome] = resultado
        if not resultado["ok"] and not resultado.get("aviso"):
            grave = True

    return {
        "ok": not grave,
        "grave": grave,
        "avisos": [nome for nome, r in resultados.items() if not r["ok"] and r.get("aviso")],
        "verificacoes": resultados,
    }
