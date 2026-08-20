"""Traz um restaurante do sistema single-tenant para dentro do Comanda ai.

É a Fase 11 do roteiro: o Borba's Burguer deixa de ser um sistema próprio e vira
o primeiro tenant. O mesmo código serve para qualquer instalação do sistema
antigo, então um cliente que já usava aquele sistema entra sem redigitar nada.

O que atravessa
---------------
Configuração da loja, categorias, produtos (com foto), adicionais, bairros de
entrega, cupons, insumos, fichas técnicas e usuários — **com a senha atual**: os
dois sistemas usam o mesmo `werkzeug.security`, então o hash é copiado e ninguém
precisa de senha nova.

O que NÃO atravessa, e por quê
------------------------------
Histórico de pedidos. No sistema antigo os itens de um pedido são um texto solto
(`"2x X-Tudo\\n1x Refri"`), e não linhas de tabela. Importar isso produziria mil
pedidos sem item, sem custo e sem lucro — que é exatamente o que alimenta CMV,
"mais vendidos" e a margem no financeiro. Em vez de encher o sistema novo de
número errado, a operação começa limpa e o histórico fica no sistema antigo para
consulta.

Duas decisões que mudam o resultado
-----------------------------------
1. **Adicional era lista global.** No sistema antigo qualquer adicional valia, e
   a tela só oferecia para a categoria "Burgers", com o nome cravado no código.
   Aqui cada produto declara os seus, então a importação recria aquele
   comportamento: os adicionais são vinculados aos produtos da categoria
   "Burgers". Está no relatório, para você conferir e ajustar depois.
2. **Categoria era texto livre.** Vira registro de verdade, na ordem em que os
   produtos aparecem no cardápio antigo.
"""

from __future__ import annotations

import shutil
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from flask import current_app

from ..extensions import db
from ..models.adicional import Adicional
from ..models.categoria import Categoria
from ..models.cupom import TIPOS_CUPOM, BairroEntrega, Cupom
from ..models.estoque import UNIDADES, FichaTecnica, Insumo
from ..models.produto import Produto
from ..models.tenant import Tenant
from ..models.usuario import Usuario

# Categoria que, no sistema antigo, era a única a aceitar adicionais.
CATEGORIA_COM_ADICIONAIS = "Burgers"


@dataclass
class Relatorio:
    """O que entrou, o que foi recusado e por quê."""

    contagens: dict[str, int] = field(default_factory=dict)
    avisos: list[str] = field(default_factory=list)
    simulado: bool = False
    slug: str = ""

    def conta(self, o_que: str, quanto: int = 1) -> None:
        self.contagens[o_que] = self.contagens.get(o_que, 0) + quanto

    def avisa(self, mensagem: str) -> None:
        self.avisos.append(mensagem)

    def linhas(self) -> list[str]:
        saida = [f"{'SIMULACAO — nada foi gravado' if self.simulado else 'Importado'}: {self.slug}"]
        for chave, valor in self.contagens.items():
            saida.append(f"  {chave:.<28} {valor}")
        if self.avisos:
            saida.append("")
            saida.append(f"  {len(self.avisos)} aviso(s):")
            saida.extend(f"    - {aviso}" for aviso in self.avisos)
        return saida


class ErroDeImportacao(Exception):
    """Impede a importação de começar. Mensagem já pronta para quem lê."""


def _abrir(caminho: str) -> sqlite3.Connection:
    arquivo = Path(caminho)
    if not arquivo.is_file():
        raise ErroDeImportacao(f"Banco do sistema antigo não encontrado: {caminho}")
    conexao = sqlite3.connect(str(arquivo))
    conexao.row_factory = sqlite3.Row
    return conexao


def _tabelas(conexao: sqlite3.Connection) -> set[str]:
    return {
        linha[0]
        for linha in conexao.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }


def _ler(conexao: sqlite3.Connection, tabela: str, existentes: set[str]) -> list[sqlite3.Row]:
    """Lê a tabela inteira, ou devolve vazio se ela não existir naquela versão.

    O sistema antigo mudou de esquema ao longo dos anos: instalações mais velhas
    não têm insumo nem cupom. Faltar tabela é motivo para pular, não para abortar
    uma importação que já trouxe o cardápio.
    """
    if tabela not in existentes:
        return []
    return list(conexao.execute(f"SELECT * FROM {tabela}"))


def _data(valor) -> datetime | None:
    if not valor:
        return None
    if isinstance(valor, datetime):
        return valor
    try:
        return datetime.fromisoformat(str(valor))
    except ValueError:
        return None


def _copiar_foto(nome_arquivo: str, pasta_origem: Path, slug: str) -> str | None:
    """Copia a foto para a pasta do tenant e devolve o caminho relativo novo.

    O nome do arquivo é mantido: ele já é único (o sistema antigo usava sufixo
    aleatório) e preservar facilita conferir o antes e o depois.
    """
    origem = pasta_origem / nome_arquivo
    if not origem.is_file():
        return None

    destino_pasta = Path(current_app.config["UPLOAD_FOLDER"]) / slug
    destino_pasta.mkdir(parents=True, exist_ok=True)
    shutil.copy2(origem, destino_pasta / origem.name)
    return f"{slug}/{origem.name}"


def importar(
    caminho_banco: str,
    *,
    slug: str,
    email_contato: str,
    nome_fantasia: str | None = None,
    plano: str = "trial",
    qtd_mesas: int = 0,
    pasta_fotos: str | None = None,
    simular: bool = False,
) -> Relatorio:
    """Cria o tenant e traz os dados. Em `simular`, desfaz tudo no fim.

    A simulação roda a importação de verdade e dá rollback: é o único jeito de
    saber se ela funciona sem descobrir no meio de uma gravação parcial.
    """
    if Tenant.query.filter_by(slug=slug).first():
        raise ErroDeImportacao(
            f"Já existe um restaurante com o slug '{slug}'. Escolha outro, ou "
            f"apague o existente antes de importar."
        )

    conexao = _abrir(caminho_banco)
    existentes = _tabelas(conexao)
    relatorio = Relatorio(simulado=simular, slug=slug)
    origem_fotos = Path(pasta_fotos) if pasta_fotos else None
    if origem_fotos and not origem_fotos.is_dir():
        raise ErroDeImportacao(f"Pasta de fotos não encontrada: {pasta_fotos}")

    try:
        config = (_ler(conexao, "loja_config", existentes) or [None])[0]
        tenant = _criar_tenant(config, slug, email_contato, nome_fantasia, plano, qtd_mesas)
        db.session.add(tenant)
        db.session.flush()
        relatorio.conta("restaurante")

        categorias = _importar_categorias(conexao, existentes, tenant, relatorio)
        produtos = _importar_produtos(
            conexao, existentes, tenant, categorias, origem_fotos, slug, relatorio, simular
        )
        _importar_adicionais(conexao, existentes, tenant, produtos, relatorio)
        _importar_bairros(conexao, existentes, tenant, relatorio)
        _importar_cupons(conexao, existentes, tenant, relatorio)
        insumos = _importar_insumos(conexao, existentes, tenant, relatorio)
        _importar_fichas(conexao, existentes, produtos, insumos, relatorio)
        _importar_usuarios(conexao, existentes, tenant, relatorio)

        if simular:
            db.session.rollback()
        else:
            db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    finally:
        conexao.close()

    return relatorio


# --------------------------------------------------------------------------- #
# Cada pedaço
# --------------------------------------------------------------------------- #


def _criar_tenant(config, slug, email_contato, nome_fantasia, plano, qtd_mesas) -> Tenant:
    def pega(campo, padrao=None):
        try:
            valor = config[campo] if config is not None else None
        except (IndexError, KeyError):
            return padrao
        return valor if valor not in (None, "") else padrao

    return Tenant(
        slug=slug,
        nome_fantasia=nome_fantasia or pega("nome", slug.title()),
        email_contato=email_contato,
        telefone_contato=str(pega("numero_whatsapp", "") or "")[:20] or None,
        plano=plano,
        status="active",
        # O sistema antigo não tinha quantidade de mesas configurável: a faixa
        # 1..30 era constante no código. Por isso vem por parâmetro.
        qtd_mesas=max(0, min(int(qtd_mesas or 0), 200)),
        tempo_estimado_min=int(pega("tempo_estimado_min", 40) or 40),
        tempo_estimado_max=int(pega("tempo_estimado_max", 60) or 60),
        margem_lucro=float(pega("margem_lucro", 60.0) or 60.0),
    )


def _importar_categorias(conexao, existentes, tenant, relatorio) -> dict[str, Categoria]:
    """Texto livre vira registro, na ordem em que aparece no cardápio antigo."""
    linhas = _ler(conexao, "produto", existentes)
    ordem_de_aparicao: list[str] = []
    for linha in linhas:
        nome = (linha["categoria"] or "").strip()
        if nome and nome not in ordem_de_aparicao:
            ordem_de_aparicao.append(nome)

    criadas = {}
    for posicao, nome in enumerate(ordem_de_aparicao):
        categoria = Categoria(tenant_id=tenant.id, nome=nome, ordem=posicao, ativa=True)
        db.session.add(categoria)
        criadas[nome] = categoria
        relatorio.conta("categorias")
    db.session.flush()
    return criadas


def _importar_produtos(
    conexao, existentes, tenant, categorias, origem_fotos, slug, relatorio, simular
) -> dict[int, Produto]:
    mapa = {}
    for linha in _ler(conexao, "produto", existentes):
        nome = (linha["nome"] or "").strip()
        if not nome:
            relatorio.avisa(f"produto id={linha['id']} sem nome: ignorado")
            continue

        categoria = categorias.get((linha["categoria"] or "").strip())
        produto = Produto(
            tenant_id=tenant.id,
            nome=nome[:100],
            descricao=(linha["descricao"] or None),
            preco=float(linha["preco"] or 0),
            categoria_id=categoria.id if categoria else None,
            disponivel=bool(linha["disponivel"]),
            combo_promocional=bool(_campo(linha, "combo_promocional", 0)),
        )

        arquivo = (linha["imagem"] or "").strip()
        if arquivo and origem_fotos and not simular:
            caminho = _copiar_foto(arquivo, origem_fotos, slug)
            if caminho:
                produto.imagem = caminho
                relatorio.conta("fotos copiadas")
            else:
                relatorio.avisa(f"foto não encontrada para '{nome}': {arquivo}")
        elif arquivo and not origem_fotos:
            relatorio.avisa(f"'{nome}' tem foto no sistema antigo, mas --fotos não foi informado")

        db.session.add(produto)
        mapa[linha["id"]] = produto
        relatorio.conta("produtos")

    db.session.flush()
    return mapa


def _importar_adicionais(conexao, existentes, tenant, produtos, relatorio) -> None:
    """Recria o vínculo que no sistema antigo era uma regra no código."""
    criados = []
    for linha in _ler(conexao, "adicional", existentes):
        nome = (linha["nome"] or "").strip()
        if not nome:
            continue
        adicional = Adicional(
            tenant_id=tenant.id,
            nome=nome[:60],
            preco=float(linha["preco"] or 0),
            disponivel=bool(_campo(linha, "disponivel", 1)),
        )
        db.session.add(adicional)
        criados.append(adicional)
        relatorio.conta("adicionais")

    if not criados:
        return
    db.session.flush()

    alvo = [
        produto
        for produto in produtos.values()
        if produto.categoria_id
        and db.session.get(Categoria, produto.categoria_id).nome == CATEGORIA_COM_ADICIONAIS
    ]
    for produto in alvo:
        produto.adicionais = list(criados)

    relatorio.avisa(
        f"os {len(criados)} adicionais foram vinculados aos {len(alvo)} produtos de "
        f"'{CATEGORIA_COM_ADICIONAIS}', que era a regra do sistema antigo. "
        f"Ajuste produto a produto se quiser diferente."
    )


def _importar_bairros(conexao, existentes, tenant, relatorio) -> None:
    vistos = set()
    for linha in _ler(conexao, "bairro_entrega", existentes):
        nome = (linha["nome"] or "").strip()
        # No sistema antigo o nome era único globalmente; aqui é único por
        # tenant. Duplicata na origem viraria erro de constraint no commit.
        if not nome or nome.lower() in vistos:
            relatorio.avisa(f"bairro duplicado ou sem nome ignorado: {nome!r}")
            continue
        vistos.add(nome.lower())
        db.session.add(
            BairroEntrega(
                tenant_id=tenant.id,
                nome=nome[:100],
                taxa=float(linha["taxa"] or 0),
                prazo_adicional_min=int(linha["prazo_adicional_min"] or 0),
                ativo=bool(linha["ativo"]),
                ordem=int(linha["ordem"] or 0),
            )
        )
        relatorio.conta("bairros")


def _importar_cupons(conexao, existentes, tenant, relatorio) -> None:
    vistos = set()
    for linha in _ler(conexao, "cupom", existentes):
        codigo = (linha["codigo"] or "").strip().upper()
        if not codigo or codigo in vistos:
            continue
        vistos.add(codigo)

        tipo = (linha["tipo"] or "").strip()
        if tipo not in TIPOS_CUPOM:
            relatorio.avisa(f"cupom {codigo}: tipo '{tipo}' desconhecido, ignorado")
            continue

        db.session.add(
            Cupom(
                tenant_id=tenant.id,
                codigo=codigo[:40],
                descricao=(linha["descricao"] or None),
                tipo=tipo,
                valor=float(linha["valor"] or 0),
                pedido_minimo=float(linha["pedido_minimo"] or 0),
                limite_usos=int(linha["limite_usos"] or 1),
                # O contador vem junto: zerar daria ao cliente usos que ele já
                # gastou no sistema antigo.
                usos_confirmados=int(linha["usos_confirmados"] or 0),
                ativo=bool(linha["ativo"]),
                permite_combo_promocional=bool(_campo(linha, "permite_combo_promocional", 0)),
                inicio_em=_data(linha["inicio_em"]),
                fim_em=_data(linha["fim_em"]),
            )
        )
        relatorio.conta("cupons")


def _importar_insumos(conexao, existentes, tenant, relatorio) -> dict[int, Insumo]:
    mapa = {}
    for linha in _ler(conexao, "insumo", existentes):
        nome = (linha["nome"] or "").strip()
        if not nome:
            continue

        unidade = (linha["unidade"] or "un").strip()
        if unidade not in UNIDADES:
            relatorio.avisa(f"insumo '{nome}': unidade '{unidade}' desconhecida, virou 'un'")
            unidade = "un"

        quantidade = float(linha["quantidade_compra"] or 0)
        if quantidade <= 0:
            # Divisão por zero aqui zeraria o custo de todo prato que usa o
            # insumo, sem avisar ninguém.
            relatorio.avisa(f"insumo '{nome}': quantidade de compra zerada, virou 1")
            quantidade = 1.0

        insumo = Insumo(
            tenant_id=tenant.id,
            nome=nome[:100],
            unidade=unidade,
            preco_compra=float(linha["preco_compra"] or 0),
            quantidade_compra=quantidade,
            estoque_atual=float(_campo(linha, "estoque_atual", 0) or 0),
            estoque_minimo=float(_campo(linha, "estoque_minimo", 0) or 0),
            controle_estoque=bool(_campo(linha, "controle_estoque", 1)),
        )
        db.session.add(insumo)
        mapa[linha["id"]] = insumo
        relatorio.conta("insumos")

    db.session.flush()
    return mapa


def _importar_fichas(conexao, existentes, produtos, insumos, relatorio) -> None:
    for linha in _ler(conexao, "ficha_tecnica", existentes):
        produto = produtos.get(linha["produto_id"])
        insumo = insumos.get(linha["insumo_id"])
        if produto is None or insumo is None:
            relatorio.avisa(
                f"ficha técnica ignorada: produto {linha['produto_id']} ou "
                f"insumo {linha['insumo_id']} não foi importado"
            )
            continue
        quantidade = float(linha["quantidade_usada"] or 0)
        if quantidade <= 0:
            continue
        db.session.add(
            FichaTecnica(produto_id=produto.id, insumo_id=insumo.id, quantidade_usada=quantidade)
        )
        relatorio.conta("linhas de ficha técnica")


def _importar_usuarios(conexao, existentes, tenant, relatorio) -> None:
    """Copia o hash da senha: os dois sistemas usam o mesmo werkzeug.security.

    É o que permite a equipe entrar no sistema novo com a senha de sempre. A
    senha em texto não existe em lugar nenhum — nem no sistema antigo.
    """
    vistos = set()
    for linha in _ler(conexao, "usuario", existentes):
        username = (linha["username"] or "").strip()
        senha = (linha["senha"] or "").strip()
        if not username or username.lower() in vistos:
            continue
        if not senha:
            relatorio.avisa(f"usuário '{username}' sem senha gravada: ignorado")
            continue
        vistos.add(username.lower())

        usuario = Usuario(
            tenant_id=tenant.id,
            nome=(linha["nome"] or username)[:100],
            username=username[:50],
            role=(linha["role"] or "admin")[:20],
            ativo=bool(_campo(linha, "ativo", 1)),
        )
        usuario.senha = senha
        db.session.add(usuario)
        relatorio.conta("usuários")

    if not vistos:
        relatorio.avisa(
            "nenhum usuário importado — crie um pela área da plataforma, senão "
            "ninguém consegue entrar no painel deste restaurante"
        )


def _campo(linha: sqlite3.Row, nome: str, padrao):
    """Lê uma coluna que pode não existir em instalações antigas."""
    try:
        valor = linha[nome]
    except (IndexError, KeyError):
        return padrao
    return padrao if valor is None else valor
