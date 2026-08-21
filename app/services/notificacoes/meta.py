"""WhatsApp Cloud API — a via oficial da Meta, com conta do próprio restaurante.

Envia sozinha, cobra por mensagem e exige conta de negócios verificada. As
credenciais são POR TENANT: cada restaurante manda pelo próprio número, com a
própria fatura. A plataforma não intermedeia o envio nem paga por ele.

Sobre modelos de mensagem (*templates*): a Meta só deixa a empresa iniciar
conversa com um texto previamente aprovado por ela. Texto livre vale apenas
dentro da janela de 24 horas aberta quando o CLIENTE escreve primeiro — e aviso
de pedido é sempre a empresa iniciando. Por isso o envio usa o modelo aprovado
quando existe um cadastrado para o evento, e, quando não existe, tenta texto
livre e devolve o erro da Meta como veio, que é o que diz à pessoa exatamente o
que falta aprovar.

Sem `requests`: só a biblioteca padrão, como no agente de impressão. Uma
dependência a menos para instalar no servidor.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from .base import Envio, ProvedorWhatsApp
from .link import numero_internacional

VERSAO_API = "v21.0"
TEMPO_LIMITE = 20

# Endereço da API. Fica em variável de módulo para o teste conseguir apontar
# para um servidor local e exercitar a chamada HTTP de verdade — cabeçalhos,
# corpo JSON e leitura do erro — sem conta na Meta.
BASE_API = "https://graph.facebook.com"


class MetaCloud(ProvedorWhatsApp):
    slug = "meta"
    nome = "WhatsApp Cloud API (oficial)"
    automatico = True

    def configurado(self, tenant) -> bool:
        return bool(
            (getattr(tenant, "whatsapp_phone_id", "") or "").strip()
            and (getattr(tenant, "whatsapp_token", "") or "").strip()
        )

    def falta_configurar(self, tenant) -> str:
        if self.configurado(tenant):
            return ""
        return (
            "Informe o ID do número e o token de acesso da sua conta WhatsApp "
            "Business para enviar automaticamente."
        )

    # ----------------------------------------------------------------- HTTP --
    def _post(self, tenant, corpo: dict) -> Envio:
        endereco = (
            f"{BASE_API.rstrip('/')}/{VERSAO_API}/"
            f"{tenant.whatsapp_phone_id.strip()}/messages"
        )
        requisicao = urllib.request.Request(
            endereco,
            data=json.dumps(corpo).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {tenant.whatsapp_token.strip()}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(requisicao, timeout=TEMPO_LIMITE) as resposta:
                dados = json.loads(resposta.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return Envio(False, erro=self._mensagem_de_erro(exc))
        except urllib.error.URLError as exc:
            return Envio(False, erro=f"Não foi possível falar com a Meta: {exc.reason}"[:700])
        except (ValueError, json.JSONDecodeError):
            return Envio(False, erro="A Meta respondeu algo que não é JSON.")

        mensagens = dados.get("messages") or [{}]
        return Envio(True, id_externo=mensagens[0].get("id"))

    @staticmethod
    def _mensagem_de_erro(exc: urllib.error.HTTPError) -> str:
        """Extrai a explicação da Meta, que é específica e útil.

        Ela costuma dizer exatamente o que fazer ("template name does not
        exist", "message failed to send because more than 24 hours have passed
        since the customer last replied"). Jogar isso fora e escrever "erro ao
        enviar" seria trocar a resposta pela pergunta.
        """
        try:
            erro = json.loads(exc.read().decode("utf-8")).get("error") or {}
        except Exception:
            return f"A Meta respondeu com erro {exc.code}."
        partes = [erro.get("message"), erro.get("error_user_msg")]
        detalhe = " ".join(parte for parte in partes if parte)
        return (detalhe or f"A Meta respondeu com erro {exc.code}.")[:700]

    # ---------------------------------------------------------------- envio --
    def enviar(self, notificacao) -> Envio:
        tenant = notificacao.tenant
        if not self.configurado(tenant):
            return Envio(False, erro=self.falta_configurar(tenant))

        numero = numero_internacional(notificacao.telefone)
        if not numero:
            return Envio(False, erro="O pedido não tem um telefone válido.")

        modelo = modelo_do_evento(tenant, notificacao.evento)
        if modelo:
            return self._post(
                tenant,
                {
                    "messaging_product": "whatsapp",
                    "to": numero,
                    "type": "template",
                    "template": {
                        "name": modelo,
                        "language": {"code": "pt_BR"},
                        "components": [
                            {
                                "type": "body",
                                "parameters": [{"type": "text", "text": notificacao.mensagem}],
                            }
                        ],
                    },
                },
            )

        return self._post(
            tenant,
            {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": numero,
                "type": "text",
                "text": {"preview_url": True, "body": notificacao.mensagem},
            },
        )


def modelos_do_tenant(tenant) -> dict[str, str]:
    """Modelos aprovados, guardados como `evento=nome`, um por linha ou vírgula.

    Mesmo formato dos limites do plano: texto simples, sem tabela nova nem JSON,
    porque são seis nomes por restaurante.
    """
    bruto = (getattr(tenant, "whatsapp_modelos", "") or "").replace("\n", ",")
    modelos: dict[str, str] = {}
    for trecho in bruto.split(","):
        evento, _, nome = trecho.partition("=")
        evento, nome = evento.strip(), nome.strip()
        if evento and nome:
            modelos[evento] = nome
    return modelos


def modelo_do_evento(tenant, evento: str) -> str:
    return modelos_do_tenant(tenant).get(evento, "")
