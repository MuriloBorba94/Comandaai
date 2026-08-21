"""Avisar o cliente pelo WhatsApp: quando, o quê e por qual caminho.

O `registro.py` decide COMO enviar. Aqui fica quando um aviso nasce e o que
acontece com ele depois.

Sobre a entrega: o aviso é gravado primeiro e enviado depois, mesmo no caminho
automático. Parece um passo a mais, mas é o que impede a mudança de status na
cozinha de ficar presa esperando a Meta responder — e é o que permite tentar de
novo quando ela não responde. Um pedido não pode deixar de avançar porque uma
API de fora está lenta.
"""

from __future__ import annotations

from datetime import datetime

from ...extensions import db
from ...models.notificacao import (
    EVENTOS_SEMPRE,
    EVENTOS,
    MAX_TENTATIVAS,
    STATUS_AGUARDANDO_ENVIO,
    STATUS_CANCELADA,
    STATUS_ENVIADA,
    STATUS_ERRO,
    STATUS_PENDENTE,
    Notificacao,
)
from ..recursos import tenant_libera
from .base import Envio, ProvedorWhatsApp
from .link import LinkWhatsApp, numero_internacional
from .registro import PADRAO, PROVEDORES, caiu_para_o_link, provedor, provedor_do_tenant
from .textos import link_de_acompanhamento, montar, primeiro_nome

__all__ = [
    "Envio",
    "PADRAO",
    "PROVEDORES",
    "ProvedorWhatsApp",
    "caiu_para_o_link",
    "cancelar_pendentes",
    "despachar",
    "despachar_pendentes",
    "enfileirar",
    "link_de_acompanhamento",
    "link_do_whatsapp",
    "marcar_enviada_na_mao",
    "montar",
    "notificacoes_do_pedido",
    "pendentes_do_tenant",
    "primeiro_nome",
    "provedor",
    "provedor_do_tenant",
]


def _ativo(tenant) -> bool:
    return tenant_libera(tenant, "whatsapp")


def enfileirar(pedido, evento: str) -> Notificacao | None:
    """Prepara o aviso de um evento. Devolve None quando não há o que avisar.

    O texto é congelado agora. Se o pedido mudar entre isto e o envio, o cliente
    recebe o que valia quando o aviso foi disparado — que é o que ele esperaria
    ao olhar o horário da mensagem.
    """
    tenant = pedido.tenant
    if not _ativo(tenant):
        return None
    if evento not in EVENTOS:
        return None
    if not numero_internacional(pedido.telefone or ""):
        # Pedido de mesa não tem telefone, e é o caso comum. Não é erro.
        return None

    escolhido = provedor_do_tenant(tenant)
    # No modo manual só confirmação e cancelamento viram aviso (ver
    # EVENTOS_SEMPRE): as etapas do meio seriam três cliques por pedido para
    # quem está atendendo, e o cliente já acompanha o resto pela página.
    if not escolhido.automatico and evento not in EVENTOS_SEMPRE:
        return None

    existente = Notificacao.query.filter_by(pedido_id=pedido.id, evento=evento).first()
    if existente is not None:
        return existente

    notificacao = Notificacao(
        tenant_id=pedido.tenant_id,
        pedido_id=pedido.id,
        evento=evento,
        telefone=pedido.telefone,
        mensagem=montar(pedido, evento),
        provedor=escolhido.slug,
        status=STATUS_PENDENTE if escolhido.automatico else STATUS_AGUARDANDO_ENVIO,
    )
    db.session.add(notificacao)
    db.session.commit()
    return notificacao


def despachar(notificacao: Notificacao) -> bool:
    """Tenta entregar um aviso. Devolve True quando ele saiu de verdade."""
    if notificacao.status == STATUS_ENVIADA:
        return False
    if (notificacao.tentativas or 0) >= MAX_TENTATIVAS:
        return False

    escolhido = provedor_do_tenant(notificacao.tenant)
    notificacao.provedor = escolhido.slug
    notificacao.tentativas = (notificacao.tentativas or 0) + 1

    resultado = escolhido.enviar(notificacao)

    if resultado.aguarda_pessoa:
        # Não é falha: é um provedor que depende de alguém clicar. Voltar a
        # contar a tentativa faria o aviso "gastar" as cinco chances sem nunca
        # ter sido oferecido a ninguém.
        notificacao.tentativas -= 1
        notificacao.status = STATUS_AGUARDANDO_ENVIO
        notificacao.erro = None
        db.session.commit()
        return False

    if resultado.ok:
        notificacao.status = STATUS_ENVIADA
        notificacao.enviada_em = datetime.now()
        notificacao.id_externo = resultado.id_externo
        notificacao.erro = None
    else:
        notificacao.status = STATUS_ERRO
        notificacao.erro = (resultado.erro or "Falha ao enviar.")[:700]
    db.session.commit()
    return resultado.ok


def marcar_enviada_na_mao(notificacao: Notificacao, *, actor: str | None = None) -> bool:
    """Registra que uma pessoa abriu o WhatsApp e mandou o aviso.

    Marcado no clique, e não com confirmação depois: exigir que o atendente
    volte à tela para dizer "mandei" garante que ninguém vai fazer isso no
    meio de um sábado cheio, e a fila ficaria cheia de avisos já enviados.
    """
    if notificacao.status == STATUS_ENVIADA:
        return False
    notificacao.status = STATUS_ENVIADA
    notificacao.enviada_em = datetime.now()
    notificacao.enviada_por = (actor or "").strip()[:80] or None
    notificacao.provedor = LinkWhatsApp.slug
    notificacao.erro = None
    db.session.commit()
    return True


def link_do_whatsapp(notificacao: Notificacao) -> str:
    """O endereço que abre a conversa com o texto pronto."""
    return LinkWhatsApp().url(notificacao)


def cancelar_pendentes(pedido) -> int:
    """Avisos que ainda não saíram e não fazem mais sentido.

    Usado quando o pedido é cancelado: "seu pedido está pronto" chegando depois
    de "seu pedido foi cancelado" é pior do que não avisar nada.
    """
    return Notificacao.query.filter(
        Notificacao.pedido_id == pedido.id,
        Notificacao.status.in_([STATUS_PENDENTE, STATUS_AGUARDANDO_ENVIO, STATUS_ERRO]),
    ).update({"status": STATUS_CANCELADA}, synchronize_session=False)


def notificacoes_do_pedido(pedido) -> list[Notificacao]:
    return (
        Notificacao.query.filter_by(pedido_id=pedido.id)
        .order_by(Notificacao.id.asc())
        .all()
    )


def pendentes_do_tenant(tenant_id: int) -> list[Notificacao]:
    """Avisos esperando uma pessoa clicar, para o painel da cozinha mostrar."""
    return (
        Notificacao.query.filter(
            Notificacao.tenant_id == tenant_id,
            Notificacao.status == STATUS_AGUARDANDO_ENVIO,
        )
        .order_by(Notificacao.id.asc())
        .all()
    )


def despachar_pendentes(limite: int = 50) -> dict[str, int]:
    """Reenvia o que ficou para trás. Chamado pelo comando agendado.

    Só mexe no que é automático: aviso esperando clique não é problema a
    resolver sozinho, é trabalho de gente que ainda não aconteceu.
    """
    fila = (
        Notificacao.query.filter(
            Notificacao.status.in_([STATUS_PENDENTE, STATUS_ERRO]),
            Notificacao.tentativas < MAX_TENTATIVAS,
        )
        .order_by(Notificacao.id.asc())
        .limit(limite)
        .all()
    )

    resultado = {"tentadas": 0, "enviadas": 0, "falharam": 0}
    for notificacao in fila:
        if not _ativo(notificacao.tenant):
            continue
        resultado["tentadas"] += 1
        if despachar(notificacao):
            resultado["enviadas"] += 1
        else:
            resultado["falharam"] += 1
    return resultado


def tentar(funcao, *args, **kwargs):
    """Roda um passo de notificação sem deixar que ele derrube o pedido.

    Mesma razão do equivalente na impressão: quando isto roda o pedido já mudou
    de status. Aviso que não saiu se reenvia; pedido travado no meio de um
    sábado, não.
    """
    from flask import current_app

    try:
        return funcao(*args, **kwargs)
    except Exception:  # noqa: BLE001 - notificação nunca derruba o pedido
        db.session.rollback()
        current_app.logger.exception("Falha ao processar notificação de WhatsApp")
        return None
