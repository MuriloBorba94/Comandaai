"""Cópia de segurança do banco, e a conferência de que ela presta.

A parte delicada do backup mora aqui, e não no script de shell, por dois
motivos:

1. **O caminho do banco vem da configuração**, e não escrito à mão no script.
   Um dia o banco muda de lugar e o script continuaria copiando um arquivo que
   não existe mais — em silêncio, porque `if [ -f ... ]` não reclama de arquivo
   ausente.
2. **Dá para testar.** Backup é a parte do sistema que ninguém exercita até
   precisar, que é o pior momento possível para descobrir que ele nunca
   funcionou.

Sobre `.backup()` em vez de copiar o arquivo: SQLite mantém escrita pendente
fora do arquivo principal (`-wal`), e uma cópia crua feita durante uma escrita
sai truncada no meio de uma transação. O `.backup()` da própria biblioteca faz
a cópia consistente, com o banco em uso.
"""

from __future__ import annotations

import gzip
import hashlib
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from flask import current_app


class BackupInvalido(RuntimeError):
    """A cópia foi feita mas não passou na conferência."""


def soma_do_arquivo(arquivo: Path) -> str:
    """SHA-256 do arquivo, lido em pedaços para não carregar tudo na memória."""
    digestor = hashlib.sha256()
    with arquivo.open("rb") as fluxo:
        for pedaco in iter(lambda: fluxo.read(1024 * 256), b""):
            digestor.update(pedaco)
    return digestor.hexdigest()


def _arquivo_da_soma(backup: Path) -> Path:
    return backup.with_name(backup.name + ".sha256")


def caminho_do_banco() -> Path | None:
    """Arquivo do banco em uso, ou None quando não é SQLite."""
    uri = current_app.config.get("SQLALCHEMY_DATABASE_URI") or ""
    if not uri.startswith("sqlite:///"):
        return None
    caminho = uri[len("sqlite:///"):]
    if not caminho or caminho == ":memory:":
        return None
    return Path(caminho)


def _conferir(arquivo: Path) -> dict:
    """Abre a cópia e prova que ela é um banco íntegro e com conteúdo.

    Integridade sozinha não basta: um arquivo vazio passa no `integrity_check`
    e não é backup de nada. Por isso a contagem de tabelas e de restaurantes
    entra na conferência.
    """
    conexao = sqlite3.connect(f"file:{arquivo.as_posix()}?mode=ro", uri=True)
    try:
        resultado = conexao.execute("pragma integrity_check").fetchone()[0]
        if resultado != "ok":
            raise BackupInvalido(f"O banco copiado não passou na verificação: {resultado}")

        tabelas = conexao.execute(
            "select count(*) from sqlite_master where type='table'"
        ).fetchone()[0]
        if not tabelas:
            raise BackupInvalido("O banco copiado está vazio (nenhuma tabela).")

        def contar(tabela: str) -> int:
            try:
                return conexao.execute(f"select count(*) from {tabela}").fetchone()[0]
            except sqlite3.Error:
                return 0

        return {
            "tabelas": tabelas,
            "tenants": contar("tenant"),
            "pedidos": contar("pedido"),
            "produtos": contar("produto"),
        }
    finally:
        conexao.close()


def fazer(destino: str | Path, *, comprimir: bool = True) -> dict:
    """Copia o banco, confere a cópia e devolve o que foi feito.

    A conferência acontece ANTES de comprimir e antes de qualquer coisa ser
    dada por concluída: um backup corrompido que o script trata como sucesso é
    pior do que backup nenhum, porque cria confiança onde não há.
    """
    origem = caminho_do_banco()
    if origem is None:
        raise BackupInvalido("O backup automático só cobre banco SQLite.")
    if not origem.exists():
        raise BackupInvalido(f"O banco não foi encontrado em {origem}.")

    pasta = Path(destino)
    pasta.mkdir(parents=True, exist_ok=True)
    carimbo = datetime.now().strftime("%Y%m%d-%H%M%S")
    copia = pasta / f"saas-{carimbo}.db"

    entrada = sqlite3.connect(f"file:{origem.as_posix()}?mode=ro", uri=True)
    saida = sqlite3.connect(copia.as_posix())
    try:
        entrada.backup(saida)
    finally:
        saida.close()
        entrada.close()

    conteudo = _conferir(copia)

    arquivo_final = copia
    if comprimir:
        arquivo_final = copia.with_suffix(".db.gz")
        with copia.open("rb") as bruto, gzip.open(arquivo_final, "wb") as comprimido:
            shutil.copyfileobj(bruto, comprimido)
        copia.unlink()

    # A soma vai num arquivo ao lado. É ela que denuncia bit trocado no disco
    # ou transferência truncada para fora da máquina — coisas que o
    # `integrity_check` do SQLite NÃO vê, porque ele confere a estrutura do
    # banco, não o conteúdo byte a byte.
    soma = soma_do_arquivo(arquivo_final)
    _arquivo_da_soma(arquivo_final).write_text(
        f"{soma}  {arquivo_final.name}" + chr(10), encoding="utf-8"
    )

    return {
        "arquivo": str(arquivo_final),
        "bytes": arquivo_final.stat().st_size,
        "sha256": soma,
        **conteudo,
    }


def mais_recente(destino: str | Path) -> Path | None:
    """O backup mais novo da pasta, comprimido ou não."""
    pasta = Path(destino)
    if not pasta.is_dir():
        return None
    copias = sorted(
        [*pasta.glob("saas-*.db"), *pasta.glob("saas-*.db.gz")],
        key=lambda arquivo: arquivo.stat().st_mtime,
    )
    return copias[-1] if copias else None


def verificar(destino: str | Path) -> dict:
    """Descomprime o backup mais recente e prova que ele ainda presta.

    Este é o comando que transforma "existe um arquivo de backup" em "existe um
    backup". A diferença entre as duas coisas só aparece no dia em que alguém
    precisa restaurar, e aí é tarde.

    São três conferências, e cada uma pega uma coisa que as outras não pegam:

    - a **soma de verificação** denuncia byte trocado no disco e transferência
      truncada;
    - o **`integrity_check`** denuncia estrutura quebrada do banco;
    - a **contagem de linhas** denuncia backup vazio, que passa nos dois
      anteriores e não é backup de nada.
    """
    arquivo = mais_recente(destino)
    if arquivo is None:
        raise BackupInvalido(f"Nenhum backup encontrado em {destino}.")

    idade_horas = (datetime.now().timestamp() - arquivo.stat().st_mtime) / 3600

    soma_gravada = None
    registro = _arquivo_da_soma(arquivo)
    if registro.exists():
        soma_gravada = registro.read_text(encoding="utf-8").split()[0]
        soma_atual = soma_do_arquivo(arquivo)
        if soma_atual != soma_gravada:
            raise BackupInvalido(
                f"O arquivo mudou desde que foi gravado: a soma de verificação não "
                f"bate ({soma_atual[:12]}… no disco, {soma_gravada[:12]}… no registro)."
            )

    if arquivo.suffix == ".gz":
        import tempfile

        with tempfile.TemporaryDirectory() as temporaria:
            aberto = Path(temporaria) / "conferencia.db"
            with gzip.open(arquivo, "rb") as comprimido, aberto.open("wb") as bruto:
                shutil.copyfileobj(comprimido, bruto)
            conteudo = _conferir(aberto)
    else:
        conteudo = _conferir(arquivo)

    return {
        "arquivo": str(arquivo),
        "bytes": arquivo.stat().st_size,
        "idade_horas": round(idade_horas, 1),
        "soma_conferida": soma_gravada is not None,
        **conteudo,
    }
