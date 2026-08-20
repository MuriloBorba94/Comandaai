"""Regras de pedido: montagem do carrinho, criação e transições de status.

Portado do order_service.py do sistema single-tenant, com as adaptações que o
multi-tenant exige e três correções em relação ao original:

1. Adicionais eram liberados por `if product.categoria == "Burgers"` e buscados
   sem filtro de dono. Agora um adicional só entra se estiver na lista do próprio
   produto (produto_adicional) — o que também resolve o filtro por tenant.
2. O número da mesa vivia dentro do texto de `endereco` ("Mesa 01") e era lido
   de volta por parsing; agora é uma coluna inteira.
3. A faixa de mesas era a constante 1..30 no código; agora vem de
   Tenant.qtd_mesas, porque cada salão é diferente.

Como no original, preço, subtotal, total e desconto são SEMPRE calculados aqui.
Nada disso é aceito do navegador — é a diferença entre um cardápio e um caixa.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models.pedido import (
    CAMPO_TIMESTAMP,
    STATUS_ATIVOS,
    STATUS_CANCELADO,
    STATUS_CONFIRMADO,
    STATUS_ENTREGUE,
    STATUS_EM_PREPARO,
    STATUS_NOVO,
    STATUS_PRONTO,
    STATUS_SAIU_ENTREGA,
    STATUS_TODOS,
    TIPO_ENTREGA,
    TIPO_MESA,
    TIPO_RETIRADA,
    TIPOS,
    Pedido,
    PedidoItem,
    PedidoItemAdicional,
)
from ..models.cupom import BairroEntrega
from ..models.impressao import TIPO_ADICAO
from ..models.produto import Produto
from .cupons import consumir as consumir_cupom
from .cupons import liberar as liberar_cupom
from .cupons import reservar_para_pedido
from .estoque import aplicar_baixa, estornar_baixa, sincronizar_baixa
from .impressao import cancelar_pendentes_do_pedido, enfileirar, garantir_comanda, tentar
from .recursos import tenant_libera

MAX_ITENS_CARRINHO = 50
MAX_QUANTIDADE_ITEM = 30

TRANSICOES_PERMITIDAS = {
    STATUS_NOVO: {STATUS_CONFIRMADO, STATUS_EM_PREPARO, STATUS_CANCELADO},
    STATUS_CONFIRMADO: {STATUS_EM_PREPARO, STATUS_CANCELADO},
    STATUS_EM_PREPARO: {STATUS_PRONTO, STATUS_CANCELADO},
    STATUS_PRONTO: {STATUS_SAIU_ENTREGA, STATUS_ENTREGUE, STATUS_CANCELADO},
    STATUS_SAIU_ENTREGA: {STATUS_ENTREGUE, STATUS_CANCELADO},
    STATUS_ENTREGUE: set(),
    STATUS_CANCELADO: set(),
}

PAGAMENTO_COMANDA = "Comanda Aberta"
FORMAS_PAGAMENTO = ("Dinheiro", "Cartão na entrega", "PIX na entrega")


def _dinheiro(valor) -> Decimal:
    """Arredonda para centavos, evitando o acúmulo de erro do float."""
    return Decimal(str(valor or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _texto(valor, limite: int) -> str:
    return (str(valor or "").strip())[:limite]


def normalizar_mesa(valor, qtd_mesas: int) -> int:
    """Valida o número da mesa contra o salão do tenant.

    Sem isso, "0" e "-1" entram no banco e a comanda vira fantasma: existe, soma
    no faturamento, mas não aparece no mapa para ninguém fechar.
    """
    if qtd_mesas <= 0:
        raise ValueError("Este restaurante não atende pedidos de mesa.")
    try:
        numero = int(str(valor).strip())
    except (TypeError, ValueError):
        raise ValueError(f"Número de mesa inválido: use um número de 1 a {qtd_mesas}.") from None
    if numero < 1 or numero > qtd_mesas:
        raise ValueError(f"A mesa {numero} não existe. Use um número de 1 a {qtd_mesas}.")
    return numero


def calcular_carrinho(tenant_id: int, carrinho) -> tuple[list[PedidoItem], Decimal]:
    """Transforma o carrinho enviado em itens validados, com preço do servidor.

    O carrinho é uma lista de dicts com produto_id, quantidade, adicionais (ids)
    e observacao. Qualquer preço que venha junto é ignorado.
    """
    if not isinstance(carrinho, list) or not carrinho:
        raise ValueError("O carrinho está vazio.")
    if len(carrinho) > MAX_ITENS_CARRINHO:
        raise ValueError("O carrinho tem itens demais.")

    itens: list[PedidoItem] = []
    subtotal = Decimal("0.00")

    for bruto in carrinho:
        try:
            produto_id = int(bruto.get("produto_id"))
            quantidade = int(bruto.get("quantidade", 1))
        except (AttributeError, TypeError, ValueError):
            raise ValueError("Item inválido no carrinho.") from None
        quantidade = max(1, min(quantidade, MAX_QUANTIDADE_ITEM))

        produto = Produto.query.filter_by(id=produto_id, tenant_id=tenant_id).first()
        if produto is None or not produto.disponivel:
            raise ValueError("Um produto do carrinho não está mais disponível.")

        # Só adicionais que ESTE produto aceita, e que estão disponíveis. Como a
        # lista vem do próprio produto (já filtrado por tenant), um id de outro
        # tenant ou de outro produto simplesmente não é encontrado.
        ids_pedidos = {int(v) for v in (bruto.get("adicionais") or []) if str(v).strip().isdigit()}
        extras = [
            adicional
            for adicional in produto.adicionais
            if adicional.id in ids_pedidos and adicional.disponivel
        ]

        preco_base = _dinheiro(produto.preco)
        preco_unitario = preco_base + sum((_dinheiro(e.preco) for e in extras), Decimal("0.00"))
        total_linha = preco_unitario * quantidade

        item = PedidoItem(
            produto_id=produto.id,
            nome=produto.nome,
            preco_base=float(preco_base),
            preco_unitario=float(preco_unitario),
            quantidade=quantidade,
            total=float(total_linha),
            observacao=_texto(bruto.get("observacao"), 180) or None,
        )
        item.adicionais = [
            PedidoItemAdicional(adicional_id=e.id, nome=e.nome, preco=float(_dinheiro(e.preco)))
            for e in extras
        ]
        itens.append(item)
        subtotal += total_linha

    return itens, subtotal


def calcular_estimativa(tenant, tipo: str, prazo_adicional: int = 0) -> tuple[int, int]:
    """Janela de tempo informada ao cliente, esticada conforme a fila atual."""
    base_min = int(tenant.tempo_estimado_min or 40)
    base_max = int(tenant.tempo_estimado_max or 60)

    ativos = Pedido.query.filter(
        Pedido.tenant_id == tenant.id, Pedido.status.in_(STATUS_ATIVOS)
    ).count()
    extra_fila = min(45, (ativos // 4) * 10)

    if tipo == TIPO_MESA:
        base_min, base_max = max(15, base_min - 20), max(30, base_max - 20)
    elif tipo == TIPO_RETIRADA:
        base_min = max(20, base_min - 10)
        base_max = max(base_min + 10, base_max - 10)

    extra = extra_fila + max(0, int(prazo_adicional or 0))
    return base_min + extra, base_max + extra


def bairros_ativos(tenant_id: int) -> list[BairroEntrega]:
    return (
        BairroEntrega.query.filter_by(tenant_id=tenant_id, ativo=True)
        .order_by(BairroEntrega.ordem, BairroEntrega.nome)
        .all()
    )


def _resolver_bairro(tenant_id: int, bairro_id) -> BairroEntrega | None:
    """Resolve o bairro escolhido, exigindo escolha só se o tenant configurou algum.

    Se o restaurante ainda não cadastrou bairros, a entrega continua possível com
    taxa zero — do contrário, um tenant novo ficaria sem poder vender entrega até
    configurar o salão inteiro.
    """
    disponiveis = bairros_ativos(tenant_id)
    if not disponiveis:
        return None

    if str(bairro_id or "").strip().isdigit():
        escolhido = BairroEntrega.query.filter_by(
            id=int(bairro_id), tenant_id=tenant_id, ativo=True
        ).first()
        if escolhido is not None:
            return escolhido
    # Com um único bairro ativo, não faz sentido exigir a escolha.
    if len(disponiveis) == 1:
        return disponiveis[0]
    raise ValueError("Selecione o bairro da entrega.")


def _proximo_numero(tenant_id: int) -> int:
    maior = db.session.query(func.max(Pedido.numero)).filter_by(tenant_id=tenant_id).scalar()
    return int(maior or 0) + 1


def pedido_por_request_id(tenant_id: int, client_request_id: str | None) -> Pedido | None:
    if not client_request_id:
        return None
    return Pedido.query.filter_by(tenant_id=tenant_id, client_request_id=client_request_id).first()


def criar_pedido(tenant, payload: dict) -> Pedido:
    """Cria um pedido a partir do payload do checkout (ou do salão).

    Devolve o pedido já existente quando o mesmo client_request_id chega de novo,
    em vez de duplicar — é o que protege contra duplo clique e reenvio.
    """
    client_request_id = _texto(payload.get("client_request_id"), 64) or None
    existente = pedido_por_request_id(tenant.id, client_request_id)
    if existente is not None:
        return existente

    cliente = _texto(payload.get("cliente"), 100)
    telefone = "".join(ch for ch in _texto(payload.get("telefone"), 20) if ch.isdigit())
    tipo = _texto(payload.get("tipo"), 20)
    observacao = _texto(payload.get("observacao"), 500) or None

    if len(cliente) < 2:
        raise ValueError("Informe o nome do cliente.")
    if tipo not in TIPOS:
        raise ValueError("Tipo de pedido inválido.")

    mesa = None
    endereco = None
    bairro = None
    pagamento = _texto(payload.get("pagamento"), 80)
    codigo_cupom = _texto(payload.get("cupom"), 40)

    if codigo_cupom and not tenant_libera(tenant, "cupons"):
        raise ValueError("Este restaurante não usa cupons de desconto.")

    if tipo == TIPO_MESA:
        mesa = normalizar_mesa(payload.get("mesa"), tenant.qtd_mesas or 0)
        if mesa_ocupada(tenant.id, mesa):
            raise ValueError(f"A mesa {mesa} já tem uma comanda aberta.")
        # Mesa não escolhe pagamento na abertura: paga no fechamento da comanda.
        pagamento = PAGAMENTO_COMANDA
        if codigo_cupom:
            # A comanda acumula itens, então um desconto percentual precisaria
            # ser recalculado a cada lançamento. Fica para uma fase futura.
            raise ValueError("Cupom não pode ser usado em comanda de mesa.")
    else:
        if len(telefone) < 10:
            raise ValueError("Informe um WhatsApp válido com DDD.")
        if not pagamento:
            raise ValueError("Escolha a forma de pagamento.")
        if tipo == TIPO_ENTREGA:
            endereco = _texto(payload.get("endereco"), 350)
            if len(endereco) < 8:
                raise ValueError("Informe o endereço completo para entrega.")
            # Sem o recurso de bairros no plano, a entrega sai com taxa zero
            # em vez de exigir uma escolha que a loja não pode configurar.
            bairro = (
                _resolver_bairro(tenant.id, payload.get("bairro_id"))
                if tenant_libera(tenant, "bairros")
                else None
            )

    itens, subtotal = calcular_carrinho(tenant.id, payload.get("carrinho"))

    # Taxa e prazo vêm do bairro cadastrado, nunca do formulário.
    taxa_entrega = _dinheiro(bairro.taxa) if bairro else Decimal("0.00")
    prazo_adicional = int(bairro.prazo_adicional_min or 0) if bairro else 0
    estimado_min, estimado_max = calcular_estimativa(tenant, tipo, prazo_adicional)

    pedido = Pedido(
        tenant_id=tenant.id,
        client_request_id=client_request_id,
        cliente=cliente,
        telefone=telefone or None,
        tipo=tipo,
        mesa=mesa,
        comanda_aberta=(tipo == TIPO_MESA),
        endereco=endereco,
        bairro_id=bairro.id if bairro else None,
        bairro_nome=bairro.nome if bairro else None,
        pagamento=pagamento,
        observacao=observacao,
        taxa_entrega=float(taxa_entrega),
        desconto=0.0,  # só o cupom altera, e sempre calculado no servidor
        status=STATUS_NOVO,
        origem=_texto(payload.get("origem"), 20) or "site",
        tempo_estimado_min=estimado_min,
        tempo_estimado_max=estimado_max,
    )
    pedido.itens = itens
    pedido.recalcular_total()

    # A numeração por tenant é calculada com MAX+1, então dois pedidos
    # simultâneos podem tentar o mesmo número. A unique constraint barra, e aqui
    # tentamos de novo com o número seguinte em vez de estourar erro na cara do
    # cliente.
    for tentativa in range(5):
        pedido.numero = _proximo_numero(tenant.id)
        db.session.add(pedido)
        try:
            db.session.flush()
        except IntegrityError:
            db.session.rollback()
            if client_request_id:
                # Pode ter sido o reenvio do mesmo pedido chegando em paralelo.
                duplicado = pedido_por_request_id(tenant.id, client_request_id)
                if duplicado is not None:
                    return duplicado
            if tentativa == 4:
                raise ValueError("Não foi possível registrar o pedido. Tente novamente.") from None
            continue

        if codigo_cupom:
            # A reserva precisa do id do pedido, por isso vem depois do flush.
            # Cupom inválido derruba o pedido inteiro: o cliente escolheu usá-lo,
            # e aplicar silenciosamente sem desconto seria pior.
            resultado = reservar_para_pedido(pedido, codigo_cupom, itens)
            if not resultado.ok:
                db.session.rollback()
                raise ValueError(resultado.mensagem)
            pedido.recalcular_total()

        db.session.commit()

        # A comanda de mesa já nasce valendo: quem lançou foi o atendente, com o
        # cliente na frente. Ninguém vai "confirmar" depois, então o papel sai
        # agora. Pedido do site espera a confirmação — ver transicionar().
        if pedido.tipo == TIPO_MESA:
            tentar(garantir_comanda, pedido)
        return pedido

    raise ValueError("Não foi possível registrar o pedido. Tente novamente.")


def transicionar(pedido: Pedido, novo_status: str, actor: str | None = None) -> Pedido:
    """Move o pedido de status respeitando o fluxo, e registra o horário."""
    novo_status = _texto(novo_status, 30)
    if novo_status not in STATUS_TODOS:
        raise ValueError("Status inválido.")

    atual = pedido.status or STATUS_NOVO
    if novo_status not in TRANSICOES_PERMITIDAS.get(atual, set()):
        raise ValueError(f"Não é possível mudar de '{atual}' para '{novo_status}'.")

    # "Saiu para entrega" não faz sentido para retirada nem para mesa. O sistema
    # original permitia, e o pedido de balcão aparecia como se estivesse na rua.
    if novo_status == STATUS_SAIU_ENTREGA and pedido.tipo != TIPO_ENTREGA:
        raise ValueError("Somente pedidos de entrega podem sair para entrega.")

    pedido.status = novo_status
    campo = CAMPO_TIMESTAMP.get(novo_status)
    if campo:
        setattr(pedido, campo, datetime.now())

    if novo_status in (STATUS_ENTREGUE, STATUS_CANCELADO):
        pedido.comanda_aberta = False

    # Cancelar devolve a vaga; qualquer outro avanço consome a reserva.
    #
    # O original só consumia em "Confirmado", mas o fluxo permite Novo -> Em
    # preparo direto: nesse caminho a reserva ficava presa para sempre,
    # bloqueando a vaga sem nunca contar como uso. consumir() é idempotente,
    # então chamar em qualquer avanço é seguro.
    if pedido.cupom_id:
        if novo_status == STATUS_CANCELADO:
            liberar_cupom(pedido)
        else:
            consumir_cupom(pedido)

    # Estoque segue a mesma regra, e pelo mesmo motivo: o original baixava apenas
    # em "Confirmado", então um pedido que pulasse direto para "Em preparo" saía
    # sem consumir insumo e com custo zerado. aplicar_baixa() é idempotente.
    if novo_status == STATUS_CANCELADO:
        estornar_baixa(pedido, usuario=actor)
    else:
        aplicar_baixa(pedido, usuario=actor)

    db.session.commit()

    if novo_status == STATUS_CANCELADO:
        # O que ainda não saiu no papel não deve sair. O que já saiu fica: aquele
        # papel está na cozinha, e o registro precisa contar isso.
        tentar(cancelar_pendentes_do_pedido, pedido)
        db.session.commit()
    elif atual == STATUS_NOVO:
        # Primeiro avanço do pedido do site: é aqui que alguém do restaurante
        # aceitou o pedido, e é aqui que a cozinha deve receber o papel. Imprimir
        # na chegada gastaria bobina com pedido que o atendente ainda vai recusar.
        tentar(garantir_comanda, pedido)

    return pedido


def adicionar_itens_comanda(pedido: Pedido, carrinho, actor: str | None = None) -> Pedido:
    """Acrescenta itens a uma comanda de mesa já aberta.

    Passa pelo mesmo calcular_carrinho do cardápio, então mesa e site produzem
    exatamente o mesmo formato de item — no original, o painel concatenava texto
    livre e somava um valor digitado à mão.
    """
    if not pedido.comanda_aberta:
        raise ValueError("Esta comanda não está aberta.")
    if pedido.status == STATUS_CANCELADO:
        raise ValueError("Este pedido foi cancelado.")

    novos, _ = calcular_carrinho(pedido.tenant_id, carrinho)
    for item in novos:
        pedido.itens.append(item)

    # A cozinha já podia ter dado o pedido por concluído; item novo volta à fila.
    if pedido.status in (STATUS_PRONTO, STATUS_ENTREGUE):
        pedido.status = STATUS_CONFIRMADO

    pedido.recalcular_total()
    # A saída de estoque do pedido é o consumo TOTAL dele: aqui a linha existente
    # cresce e só a diferença sai do saldo.
    sincronizar_baixa(pedido, usuario=actor)
    db.session.commit()

    # Só o que entrou agora vai para a cozinha. Reimprimir a comanda inteira
    # faria o cozinheiro repetir o que já entregou.
    tentar(enfileirar, pedido, TIPO_ADICAO, novos)
    return pedido


def fechar_comanda(pedido: Pedido, pagamento: str) -> Pedido:
    """Fecha a comanda da mesa registrando como o cliente pagou."""
    if not pedido.comanda_aberta:
        raise ValueError("Esta comanda já foi fechada.")
    forma = _texto(pagamento, 80)
    if not forma:
        raise ValueError("Escolha a forma de pagamento.")
    if not pedido.itens:
        raise ValueError("Não é possível fechar uma comanda sem itens.")

    pedido.pagamento = forma
    pedido.comanda_aberta = False
    pedido.status = STATUS_ENTREGUE
    pedido.entregue_em = datetime.now()
    pedido.recalcular_total()
    db.session.commit()
    return pedido


def mesa_ocupada(tenant_id: int, mesa: int) -> bool:
    return (
        Pedido.query.filter_by(tenant_id=tenant_id, mesa=mesa, comanda_aberta=True).first() is not None
    )


def mesas_ativas(tenant_id: int) -> dict[int, Pedido]:
    """Mesas com comanda aberta, indexadas pelo número da mesa."""
    abertas = (
        Pedido.query.filter_by(tenant_id=tenant_id, comanda_aberta=True)
        .order_by(Pedido.created_at.desc())
        .all()
    )
    return {pedido.mesa: pedido for pedido in abertas if pedido.mesa}


def versao_da_fila(tenant_id: int) -> str:
    """Impressão digital da fila de um tenant, para o painel saber se mudou.

    Combina quantos pedidos estão ativos com o horário da última alteração.
    Pedido novo muda a contagem; mudança de status ou item lançado numa comanda
    mexem no `updated_at` (o total do pedido é recalculado). Comparar essa string
    é muito mais barato que reenviar o painel inteiro a cada poucos segundos.
    """
    ativos = Pedido.query.filter(
        Pedido.tenant_id == tenant_id, Pedido.status.in_(STATUS_ATIVOS)
    )
    quantidade = ativos.count()
    ultima = (
        db.session.query(func.max(Pedido.updated_at))
        .filter(Pedido.tenant_id == tenant_id, Pedido.status.in_(STATUS_ATIVOS))
        .scalar()
    )
    return f"{quantidade}:{ultima.isoformat() if ultima else '-'}"


def total_aguardando(tenant_id: int) -> int:
    """Pedidos ainda em "Novo" — os que ninguém olhou ainda."""
    return Pedido.query.filter_by(tenant_id=tenant_id, status=STATUS_NOVO).count()


def pedidos_ativos(tenant_id: int) -> list[Pedido]:
    return (
        Pedido.query.filter(Pedido.tenant_id == tenant_id, Pedido.status.in_(STATUS_ATIVOS))
        .order_by(Pedido.created_at.asc())
        .all()
    )


def proximos_status(pedido: Pedido) -> list[str]:
    """Transições que a cozinha pode oferecer para este pedido, já filtradas."""
    permitidos = TRANSICOES_PERMITIDAS.get(pedido.status or STATUS_NOVO, set())
    if pedido.tipo != TIPO_ENTREGA:
        permitidos = permitidos - {STATUS_SAIU_ENTREGA}
    # Mantém a ordem natural do fluxo, com Cancelado no fim.
    ordem = [s for s in STATUS_TODOS if s != STATUS_CANCELADO] + [STATUS_CANCELADO]
    return [status for status in ordem if status in permitidos]
