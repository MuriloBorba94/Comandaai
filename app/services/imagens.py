"""Recebe, valida e otimiza as imagens do sistema (produto e logo), por tenant.

Portado do image_service.py do sistema single-tenant, com duas mudanças que o
multi-tenant exige:

1. Cada tenant grava na sua própria pasta (static/uploads/<slug>/), então uma
   imagem nunca sobrescreve a de outro restaurante — nem por colisão de nome,
   nem por id de produto repetido entre tenants.
2. A remoção valida que o caminho está de fato dentro de uploads/ antes de
   apagar, para que um valor inesperado no banco não vire exclusão de arquivo
   fora dali.

Como no original, a validação é feita pelo conteúdo real do arquivo (Pillow), e
não pela extensão informada pelo navegador — que é dado do cliente, portanto não
confiável.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from flask import current_app
from PIL import Image, ImageOps, UnidentifiedImageError
from werkzeug.utils import secure_filename

Image.MAX_IMAGE_PIXELS = 45_000_000

MAX_SIZE = (1400, 1400)
# Logo é exibida em 42x42 na sidebar e até 170px na vitrine; não precisa ser
# grande, e limitar economiza banda de quem acessa pelo celular.
MAX_SIZE_LOGO = (400, 400)
QUALIDADE = 84
MIN_LADO = 120
MIN_LADO_LOGO = 48


@dataclass(frozen=True)
class ImagemSalva:
    caminho_relativo: str
    largura: int
    altura: int
    bytes: int


def _pasta_uploads() -> Path:
    return Path(current_app.config["UPLOAD_FOLDER"]).resolve()


def salvar_imagem_produto(file_storage, *, tenant_slug: str, produto_id: int | None = None) -> ImagemSalva | None:
    """Valida, redimensiona e grava a imagem como WebP na pasta do tenant.

    Devolve None quando nenhum arquivo foi enviado (campo vazio no formulário) e
    levanta ValueError com mensagem pronta para o usuário quando o arquivo é
    inválido.
    """
    return _salvar(
        file_storage,
        tenant_slug=tenant_slug,
        prefixo="produto",
        sufixo=produto_id,
        maximo=MAX_SIZE,
        lado_minimo=MIN_LADO,
    )


def salvar_logo_tenant(file_storage, *, tenant_slug: str) -> ImagemSalva | None:
    """Grava a logo do restaurante na pasta dele, como qualquer outra imagem.

    O nome do arquivo carrega um sufixo aleatório, então trocar a logo gera um
    caminho novo e o navegador não serve a antiga de cache.
    """
    return _salvar(
        file_storage,
        tenant_slug=tenant_slug,
        prefixo="logo",
        sufixo=None,
        maximo=MAX_SIZE_LOGO,
        lado_minimo=MIN_LADO_LOGO,
    )


def _salvar(file_storage, *, tenant_slug, prefixo, sufixo, maximo, lado_minimo):
    if not file_storage or not getattr(file_storage, "filename", ""):
        return None

    # O slug vem do tenant resolvido pelo subdomínio, mas passa por
    # secure_filename de todo jeito: é ele que forma um trecho de caminho.
    pasta_tenant = secure_filename(tenant_slug) or "sem-tenant"

    try:
        file_storage.stream.seek(0)
        with Image.open(file_storage.stream) as origem:
            origem.load()
            imagem = ImageOps.exif_transpose(origem)
            if imagem.width < lado_minimo or imagem.height < lado_minimo:
                raise ValueError(
                    f"A imagem é pequena demais. Use pelo menos {lado_minimo} x {lado_minimo} pixels."
                )
            if imagem.width * imagem.height > Image.MAX_IMAGE_PIXELS:
                raise ValueError("A imagem tem resolução muito alta.")

            # Preserva transparência só quando ela existe de fato.
            tem_alpha = imagem.mode in {"RGBA", "LA"} or (
                imagem.mode == "P" and "transparency" in imagem.info
            )
            imagem = imagem.convert("RGBA" if tem_alpha else "RGB")
            imagem.thumbnail(maximo, Image.Resampling.LANCZOS)

            marca = sufixo if sufixo is not None else "x"
            nome_arquivo = f"{prefixo}_{marca}_{uuid4().hex[:12]}.webp"
            destino = _pasta_uploads() / pasta_tenant / nome_arquivo
            destino.parent.mkdir(parents=True, exist_ok=True)
            imagem.save(destino, format="WEBP", quality=QUALIDADE, method=6, optimize=True)

            return ImagemSalva(
                caminho_relativo=f"{pasta_tenant}/{nome_arquivo}",
                largura=imagem.width,
                altura=imagem.height,
                bytes=destino.stat().st_size,
            )
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        if isinstance(exc, ValueError):
            raise
        raise ValueError("O arquivo enviado não é uma imagem válida ou está corrompido.") from exc
    finally:
        try:
            file_storage.stream.seek(0)
        except Exception:  # noqa: BLE001 - stream já consumido não é problema aqui
            pass


def remover_imagem(caminho_relativo: str | None) -> bool:
    """Apaga uma imagem gravada aqui. Devolve True se o arquivo foi removido.

    Recusa qualquer caminho que, resolvido, caia fora da pasta de uploads —
    barreira contra ".." vindo de um valor corrompido ou manipulado no banco.
    """
    if not caminho_relativo:
        return False

    base = _pasta_uploads()
    alvo = (base / caminho_relativo).resolve()
    if not alvo.is_relative_to(base):
        current_app.logger.warning("Recusando remover imagem fora de uploads/: %r", caminho_relativo)
        return False

    try:
        alvo.unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError as exc:
        current_app.logger.warning("Falha ao remover imagem %r: %s", caminho_relativo, exc)
        return False
