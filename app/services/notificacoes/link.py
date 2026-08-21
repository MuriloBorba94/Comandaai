"""Link do WhatsApp: o sistema escreve, uma pessoa clica.

Custa zero, funciona no primeiro dia e não depende de conta em lugar nenhum —
é o `wa.me`, que abre a conversa com o texto já digitado. Quem está no balcão
confere e envia.

É o provedor padrão, e é de propósito que ele não exige configuração: um
restaurante que acabou de entrar na plataforma consegue avisar o cliente hoje,
sem verificação de conta de negócios nem custo por mensagem.

O que ele não faz é enviar sozinho. Por isso `enviar()` devolve
`aguarda_pessoa=True` em vez de fingir sucesso — a notificação fica visível
esperando o clique, e não sumindo como se já tivesse ido.
"""

from __future__ import annotations

from urllib.parse import quote

from .base import Envio, ProvedorWhatsApp


def somente_digitos(telefone: str) -> str:
    return "".join(caractere for caractere in (telefone or "") if caractere.isdigit())


def numero_internacional(telefone: str) -> str:
    """Deixa o número no formato que o wa.me espera: país + DDD + número.

    O cadastro guarda só dígitos, normalmente com 10 ou 11 (DDD + número). O
    wa.me precisa do código do país junto, senão abre uma conversa com um número
    que não existe — e o atendente descobre isso com o cliente esperando.
    """
    digitos = somente_digitos(telefone)
    if digitos.startswith("55") and len(digitos) in (12, 13):
        return digitos
    if len(digitos) in (10, 11):
        return "55" + digitos
    return digitos


class LinkWhatsApp(ProvedorWhatsApp):
    slug = "link"
    nome = "Link do WhatsApp (grátis)"
    automatico = False

    def configurado(self, tenant) -> bool:
        # Não há o que configurar: é só um endereço.
        return True

    def falta_configurar(self, tenant) -> str:
        return ""

    def url(self, notificacao) -> str:
        numero = numero_internacional(notificacao.telefone)
        if not numero:
            return ""
        return f"https://wa.me/{numero}?text={quote(notificacao.mensagem)}"

    def enviar(self, notificacao) -> Envio:
        if not numero_internacional(notificacao.telefone):
            return Envio(False, erro="O pedido não tem um telefone válido.")
        return Envio(False, aguarda_pessoa=True)
