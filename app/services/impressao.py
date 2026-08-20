"""Fila de impressão da cozinha e o pareamento do agente do restaurante.

O papel sai por um programa que roda no computador do estabelecimento (pasta
`agente/`) e que **consulta** o servidor de tempos em tempos. É de propósito:
assim a impressora não precisa de IP fixo, porta aberta no roteador nem nada
que o dono do restaurante tenha que pedir para a operadora — a rede dele nunca
recebe conexão de fora.

Sobre o texto da comanda: ele é montado aqui, no servidor, e não no agente. O
agente é burro por decisão — recebe texto pronto e manda para a impressora. Se
a formatação morasse nele, corrigir uma linha torta exigiria atualizar o
programa em cada restaurante.
"""

from __future__ import annotations

import hashlib
import secrets
import textwrap
from datetime import datetime, timedelta

from ..extensions import db
from ..models.impressao import (
    MAX_TENTATIVAS,
    SEGUNDOS_PARA_LIBERAR_RESERVA,
    STATUS_CANCELADO,
    STATUS_ERRO,
    STATUS_IMPRESSO,
    STATUS_IMPRIMINDO,
    STATUS_PENDENTE,
    TIPO_ADICAO,
    TIPO_COMANDA,
    TIPO_FECHAMENTO,
    TIPO_TESTE,
    AgenteImpressao,
    ImpressaoJob,
)
from ..models.pedido import TIPO_ENTREGA, TIPO_MESA
from .recursos import tenant_libera

# Largura da bobina de 80mm em modo texto. É a mesma do sistema original, e a
# das impressoras térmicas de balcão em geral.
LARGURA = 42

# Codificação que a impressora entende. cp850 é o que a Daruma e as genéricas
# aceitam com acento; o agente pode sobrescrever no arquivo de configuração
# quando o modelo do restaurante for diferente.
CODIFICACAO = "cp850"


# --------------------------------------------------------------------------- #
# Pareamento do agente
# --------------------------------------------------------------------------- #


def _hash(token: str) -> str:
    return hashlib.sha256(token.strip().encode("utf-8")).hexdigest()


def agente_do_tenant(tenant_id: int) -> AgenteImpressao | None:
    return AgenteImpressao.query.filter_by(tenant_id=tenant_id).first()


def parear(tenant) -> str:
    """Gera um código de ativação novo e devolve o texto UMA vez.

    Gerar de novo invalida o código anterior — é assim que se tira o acesso de
    um computador que saiu do restaurante, sem precisar de outra tela.
    """
    token = secrets.token_urlsafe(32)
    agente = agente_do_tenant(tenant.id)
    if agente is None:
        agente = AgenteImpressao(tenant_id=tenant.id)
        db.session.add(agente)

    agente.token_hash = _hash(token)
    # Zera o que o agente antigo tinha informado: o código novo vai para outra
    # máquina, e mostrar na tela o nome do computador anterior faria o dono
    # achar que já está pareado.
    agente.nome = ""
    agente.impressora = ""
    agente.versao = ""
    agente.ultimo_contato = None

    _devolver_reservas(tenant.id)
    db.session.commit()
    return token


def desparear(tenant) -> bool:
    """Desliga a impressão remota deste restaurante."""
    agente = agente_do_tenant(tenant.id)
    if agente is None:
        return False
    db.session.delete(agente)
    _devolver_reservas(tenant.id)
    db.session.commit()
    return True


def _devolver_reservas(tenant_id: int) -> None:
    """Solta trabalhos presos em "imprimindo" sem confirmação."""
    ImpressaoJob.query.filter_by(tenant_id=tenant_id, status=STATUS_IMPRIMINDO).update(
        {"status": STATUS_PENDENTE, "claim_token": None, "reservado_em": None},
        synchronize_session=False,
    )


def autenticar(tenant, token: str | None) -> AgenteImpressao | None:
    """Confere o código apresentado pelo agente.

    O tenant vem do subdomínio que o agente chamou, e o código precisa ser o
    daquele restaurante: um código válido apontado para o endereço do vizinho
    não abre nada.
    """
    if not token:
        return None
    agente = agente_do_tenant(tenant.id)
    if agente is None or not agente.token_hash:
        return None
    if not secrets.compare_digest(_hash(token), agente.token_hash):
        return None
    return agente


def registrar_contato(agente: AgenteImpressao, payload: dict | None = None) -> AgenteImpressao:
    """Anota que o agente falou com o servidor, e com que máquina e impressora."""
    payload = payload or {}
    agente.ultimo_contato = datetime.now()
    nome = str(payload.get("agent_name") or "").strip()
    impressora = str(payload.get("printer_name") or "").strip()
    versao = str(payload.get("version") or "").strip()
    if nome:
        agente.nome = nome[:100]
    if impressora:
        agente.impressora = impressora[:255]
    if versao:
        agente.versao = versao[:20]
    return agente


def situacao(tenant) -> dict:
    """Resumo do pareamento para a tela de impressão."""
    agente = agente_do_tenant(tenant.id)
    if agente is None:
        return {
            "pareado": False,
            "online": False,
            "agente": None,
            "segundos": None,
            "aviso": "Nenhum computador está pareado. Gere o código de ativação para começar.",
        }

    segundos = agente.segundos_desde_contato
    if agente.online:
        aviso = None
    elif segundos is None:
        aviso = "O código foi gerado, mas nenhum computador se conectou ainda."
    else:
        aviso = f"O agente está sem se conectar há {_tempo_legivel(segundos)}."

    return {
        "pareado": True,
        "online": agente.online,
        "agente": agente,
        "segundos": segundos,
        "aviso": aviso,
    }


def _tempo_legivel(segundos: int) -> str:
    if segundos < 90:
        return f"{segundos} segundos"
    if segundos < 5400:
        return f"{round(segundos / 60)} minutos"
    if segundos < 172800:
        return f"{round(segundos / 3600)} horas"
    return f"{round(segundos / 86400)} dias"


# --------------------------------------------------------------------------- #
# Texto que sai no papel
# --------------------------------------------------------------------------- #


def _dinheiro(valor) -> str:
    return f"R$ {float(valor or 0):.2f}".replace(".", ",")


def _linha_dupla(esquerda: str, direita: str) -> str:
    """Rótulo à esquerda, valor à direita, dentro da largura da bobina."""
    espaco = LARGURA - len(esquerda) - len(direita)
    if espaco < 1:
        return f"{esquerda} {direita}"
    return esquerda + " " * espaco + direita


def _quebrar(texto: str, recuo: str = "") -> list[str]:
    """Quebra texto longo na largura do papel.

    Sem isto a impressora quebra sozinha em qualquer ponto, e um endereço vira
    duas linhas cortadas no meio de uma palavra.
    """
    texto = (texto or "").strip()
    if not texto:
        return []
    return textwrap.wrap(
        texto, width=LARGURA, initial_indent=recuo, subsequent_indent=recuo + "  "
    ) or [recuo + texto]


def _bloco_de_itens(itens) -> list[str]:
    """Os itens como a cozinha precisa ler: quantidade, nome, extras recuados."""
    linhas: list[str] = []
    for item in itens:
        linhas.extend(_quebrar(f"{item.quantidade}x {item.nome}"))
        for adicional in item.adicionais:
            linhas.extend(_quebrar(f"+ {adicional.nome}", recuo="   "))
        if item.observacao:
            linhas.extend(_quebrar(f"OBS: {item.observacao}", recuo="   "))
    return linhas


def _cabecalho(tenant, subtitulo: str) -> list[str]:
    nome = (tenant.nome_fantasia or tenant.slug or "").upper()
    return [
        *[linha.center(LARGURA) for linha in _quebrar(nome)],
        subtitulo.center(LARGURA),
        "=" * LARGURA,
    ]


def _identificacao(pedido) -> list[str]:
    momento = pedido.created_at or datetime.now()
    linhas = [
        _linha_dupla(f"PEDIDO #{pedido.numero}", momento.strftime("%d/%m %H:%M")),
        "-" * LARGURA,
        *_quebrar(f"CLIENTE: {pedido.cliente}"),
        f"TIPO: {pedido.tipo}",
    ]
    if pedido.tipo == TIPO_MESA and pedido.mesa:
        linhas.append(f"MESA: {pedido.mesa:02d}")
    if pedido.tipo == TIPO_ENTREGA:
        linhas.extend(_quebrar(f"ENDERECO: {pedido.endereco or '-'}"))
        if pedido.bairro_nome:
            linhas.extend(_quebrar(f"BAIRRO: {pedido.bairro_nome}"))
        if pedido.telefone:
            linhas.append(f"TELEFONE: {pedido.telefone}")
    return linhas


def _totais(pedido) -> list[str]:
    linhas = [_linha_dupla("Subtotal", _dinheiro(pedido.subtotal))]
    if pedido.taxa_entrega:
        linhas.append(_linha_dupla("Taxa de entrega", _dinheiro(pedido.taxa_entrega)))
    if pedido.desconto:
        rotulo = f"Desconto ({pedido.cupom_codigo})" if pedido.cupom_codigo else "Desconto"
        linhas.append(_linha_dupla(rotulo, "-" + _dinheiro(pedido.desconto)))
    linhas.append("-" * LARGURA)
    linhas.append(_linha_dupla("TOTAL", _dinheiro(pedido.total)))

    # "Comanda Aberta" não é forma de pagamento: é o valor que o sistema guarda
    # enquanto a mesa ainda não escolheu. Imprimir isso na conta que vai para o
    # cliente é ruído — ele está justamente decidindo como vai pagar.
    from .pedidos import PAGAMENTO_COMANDA

    if pedido.pagamento and pedido.pagamento != PAGAMENTO_COMANDA:
        linhas.append(f"PAGAMENTO: {pedido.pagamento}")
    return linhas


def montar_comanda(pedido, tipo: str = TIPO_COMANDA, itens=None) -> str:
    """Monta o texto de um trabalho de impressão.

    `itens` existe para a comanda de acréscimo: quando a mesa pede mais, sai no
    papel só o que entrou agora.
    """
    tenant = pedido.tenant
    itens = list(itens if itens is not None else pedido.itens)

    if tipo == TIPO_ADICAO:
        subtitulo = "ITENS ADICIONAIS"
    elif tipo == TIPO_FECHAMENTO:
        subtitulo = "CONFERENCIA DE CONSUMO"
    else:
        subtitulo = "COMANDA DE PRODUCAO"

    linhas = [*_cabecalho(tenant, subtitulo), *_identificacao(pedido), "=" * LARGURA]

    linhas.append("ITENS LANCADOS AGORA" if tipo == TIPO_ADICAO else "ITENS")
    linhas.append("-" * LARGURA)
    linhas.extend(_bloco_de_itens(itens) or ["(sem itens)"])
    linhas.append("=" * LARGURA)

    if pedido.observacao and tipo != TIPO_ADICAO:
        linhas.extend(_quebrar(f"OBS: {pedido.observacao}"))
        linhas.append("=" * LARGURA)

    # A comanda de acréscimo não leva total: ela vai para a cozinha, e um valor
    # parcial no papel é lido como se fosse a conta da mesa.
    if tipo != TIPO_ADICAO:
        linhas.extend(_totais(pedido))
        linhas.append("=" * LARGURA)

    if tipo == TIPO_FECHAMENTO:
        linhas.append("NAO E DOCUMENTO FISCAL".center(LARGURA))
        linhas.append("=" * LARGURA)

    linhas.append("")
    linhas.append("")
    return "\n".join(linhas)


def montar_teste(tenant) -> str:
    agora = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    return "\n".join(
        [
            *_cabecalho(tenant, "TESTE DE IMPRESSAO"),
            f"Data: {agora}",
            "",
            "Se voce esta lendo isto no papel, o",
            "computador do balcao esta pareado e a",
            "impressora esta funcionando.",
            "-" * LARGURA,
            "Confira tambem:",
            "1) Os acentos abaixo sairam certos?",
            "   ACAO, PAO, CAFE, PICOLE",
            "2) A linha abaixo cabe inteira no papel?",
            "|" + "-" * (LARGURA - 2) + "|",
            "=" * LARGURA,
            "",
            "",
        ]
    )


# --------------------------------------------------------------------------- #
# Fila
# --------------------------------------------------------------------------- #


def enfileirar(pedido, tipo: str = TIPO_COMANDA, itens=None, *, forcar: bool = False):
    """Coloca uma comanda na fila. Devolve o trabalho, ou None quando não há a quem entregar.

    Automaticamente só entra na fila quem tem o recurso no plano E um agente
    pareado. Sem a segunda condição a fila cresceria em silêncio em todo
    restaurante que nunca instalou o agente, e no dia em que alguém instalasse
    receberia um mês de comandas velhas de uma vez.

    `forcar` é para o botão de reimprimir: ali quem pediu foi uma pessoa, e um
    silêncio seria lido como defeito. O plano continua valendo — o que `forcar`
    dispensa é a exigência de o agente já estar pareado.
    """
    tenant = pedido.tenant
    if not tenant_libera(tenant, "impressao"):
        return None
    if not forcar and agente_do_tenant(tenant.id) is None:
        return None

    job = ImpressaoJob(
        tenant_id=pedido.tenant_id,
        pedido_id=pedido.id,
        tipo=tipo,
        titulo=f"Pedido #{pedido.numero}",
        conteudo=montar_comanda(pedido, tipo=tipo, itens=itens),
    )
    db.session.add(job)
    db.session.commit()
    return job


def garantir_comanda(pedido):
    """Enfileira a comanda de produção do pedido — uma vez só.

    O fluxo permite Novo → Confirmado → Em preparo, e o pedido de mesa já nasce
    valendo. Sem esta guarda a mesma comanda sairia duas ou três vezes conforme
    o caminho que o atendente clicasse.
    """
    ja_existe = ImpressaoJob.query.filter(
        ImpressaoJob.pedido_id == pedido.id,
        ImpressaoJob.tipo == TIPO_COMANDA,
        ImpressaoJob.status != STATUS_CANCELADO,
    ).first()
    if ja_existe is not None:
        return None
    return enfileirar(pedido, TIPO_COMANDA)


def tentar(funcao, *args, **kwargs):
    """Roda um enfileiramento sem deixar que ele derrube a venda.

    Quando isto roda o pedido já está gravado. Um erro aqui — banco ocupado,
    migration ainda não aplicada — não pode virar tela de erro para quem acabou
    de comprar: comanda não impressa se reimprime num clique; pedido perdido no
    meio do checkout, não.
    """
    from flask import current_app

    try:
        return funcao(*args, **kwargs)
    except Exception:  # noqa: BLE001 - impressão nunca derruba o pedido
        db.session.rollback()
        current_app.logger.exception("Falha ao enfileirar impressão")
        return None


def enfileirar_teste(tenant):
    job = ImpressaoJob(
        tenant_id=tenant.id,
        tipo=TIPO_TESTE,
        titulo="Teste de impressao",
        conteudo=montar_teste(tenant),
    )
    db.session.add(job)
    db.session.commit()
    return job


def reservar_proximo(agente: AgenteImpressao, payload: dict | None = None) -> dict | None:
    """Entrega o próximo trabalho ao agente, reservado no nome dele.

    A reserva é o que impede a mesma comanda de sair duas vezes quando a rede
    cai entre a entrega e a confirmação: o trabalho fica marcado, e só volta
    para a fila depois do tempo de espera.

    O heartbeat mora aqui, e não só na rota, para que a tela do painel diga
    "Conectado" por consequência de o agente estar de fato trabalhando — não
    por causa de uma chamada que alguém pode esquecer de fazer.
    """
    registrar_contato(agente, payload)

    limite = datetime.now() - timedelta(seconds=SEGUNDOS_PARA_LIBERAR_RESERVA)
    job = (
        ImpressaoJob.query.filter(
            ImpressaoJob.tenant_id == agente.tenant_id,
            ImpressaoJob.tentativas < MAX_TENTATIVAS,
            db.or_(
                ImpressaoJob.status.in_([STATUS_PENDENTE, STATUS_ERRO]),
                db.and_(
                    ImpressaoJob.status == STATUS_IMPRIMINDO,
                    ImpressaoJob.reservado_em < limite,
                ),
            ),
        )
        .order_by(ImpressaoJob.id.asc())
        .first()
    )

    if job is None:
        db.session.commit()
        return None

    claim_token = secrets.token_urlsafe(24)
    job.status = STATUS_IMPRIMINDO
    job.claim_token = claim_token
    job.reservado_em = datetime.now()
    job.tentativas = (job.tentativas or 0) + 1
    job.erro = None
    db.session.commit()

    return {
        "job_id": job.id,
        "claim_token": claim_token,
        "job_name": job.titulo or f"Comanda {job.id}",
        "content": job.conteudo,
        "encoding": CODIFICACAO,
    }


def concluir(
    agente: AgenteImpressao, job_id: int, claim_token: str, ok: bool, erro: str = ""
) -> ImpressaoJob:
    """Registra o resultado informado pelo agente."""
    job = ImpressaoJob.query.filter_by(id=job_id, tenant_id=agente.tenant_id).first()
    if job is None:
        raise ValueError("Trabalho de impressão não encontrado.")
    if job.status != STATUS_IMPRIMINDO or not job.claim_token:
        raise ValueError("Este trabalho não está reservado para impressão.")
    if not secrets.compare_digest(job.claim_token, claim_token or ""):
        raise ValueError("A reserva deste trabalho expirou.")

    if ok:
        job.status = STATUS_IMPRESSO
        job.impresso_em = datetime.now()
        job.erro = None
    else:
        job.status = STATUS_ERRO
        job.erro = (erro or "O agente não conseguiu imprimir.")[:500]
    job.claim_token = None
    job.reservado_em = None
    db.session.commit()
    return job


def cancelar(tenant, job_id: int) -> bool:
    """Tira da fila um trabalho que não deve mais sair (pedido cancelado, papel acabou)."""
    job = ImpressaoJob.query.filter_by(id=job_id, tenant_id=tenant.id).first()
    if job is None or job.status == STATUS_IMPRESSO:
        return False
    job.status = STATUS_CANCELADO
    job.claim_token = None
    job.reservado_em = None
    db.session.commit()
    return True


def cancelar_pendentes_do_pedido(pedido) -> int:
    """Ao cancelar um pedido, o que ainda não saiu no papel não deve sair.

    O que já foi impresso fica como está: aquele papel está na cozinha, e o
    registro precisa contar isso.
    """
    return ImpressaoJob.query.filter(
        ImpressaoJob.tenant_id == pedido.tenant_id,
        ImpressaoJob.pedido_id == pedido.id,
        ImpressaoJob.status.in_([STATUS_PENDENTE, STATUS_ERRO]),
    ).update(
        {"status": STATUS_CANCELADO, "claim_token": None, "reservado_em": None},
        synchronize_session=False,
    )


def fila(tenant_id: int, limite: int = 20) -> list[ImpressaoJob]:
    return (
        ImpressaoJob.query.filter_by(tenant_id=tenant_id)
        .order_by(ImpressaoJob.id.desc())
        .limit(limite)
        .all()
    )


def pendentes(tenant_id: int) -> int:
    return ImpressaoJob.query.filter(
        ImpressaoJob.tenant_id == tenant_id,
        ImpressaoJob.status.in_([STATUS_PENDENTE, STATUS_IMPRIMINDO, STATUS_ERRO]),
    ).count()
