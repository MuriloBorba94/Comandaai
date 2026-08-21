"""Desenha o BR Code como QR, em SVG, para ir dentro da página.

SVG e não PNG, e embutido em vez de servido por uma rota, por três motivos
concretos:

1. **Não precisa de mais uma rota protegida.** O código de pagamento é do
   pedido; uma imagem em `/pedido/<token>/qr.png` exigiria repetir a checagem
   do token. Dentro da página, ele herda a proteção que a página já tem.
2. **Fica nítido em qualquer tela.** Leitor de QR erra mais em imagem
   redimensionada, e o celular do cliente vai ampliar.
3. **Não depende de biblioteca de imagem.** É texto.

Custa cerca de 13 KB de marcação, quase toda repetida — o que o gzip do
servidor reduz a quase nada.
"""

from __future__ import annotations

import io
import re

# A declaração XML no meio de um documento HTML não serve para nada e alguns
# navegadores reclamam dela; sai fora.
_DECLARACAO = re.compile(r"^<\?xml[^>]*\?>\s*")
# O tamanho fixo em milímetros vem da biblioteca. Trocado por 100% para o QR
# acompanhar a caixa em que for colocado, no celular e no computador.
_TAMANHO = re.compile(r'\swidth="[^"]*"\s+height="[^"]*"')


def svg(codigo: str) -> str:
    """Devolve o `<svg>` do código, pronto para ir no template."""
    import qrcode
    from qrcode.image.svg import SvgPathImage

    # border=2 é o mínimo confortável: sem margem branca em volta, muitos
    # leitores não encontram o código.
    imagem = qrcode.make(codigo, image_factory=SvgPathImage, box_size=10, border=2)
    buffer = io.BytesIO()
    imagem.save(buffer)
    marcacao = buffer.getvalue().decode("utf-8")

    marcacao = _DECLARACAO.sub("", marcacao)
    marcacao = _TAMANHO.sub(' width="100%" height="100%"', marcacao, count=1)
    return marcacao.replace(
        "<svg ", '<svg role="img" aria-label="QR Code para pagamento PIX" ', 1
    )
