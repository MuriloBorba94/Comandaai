"""Cobrança da mensalidade pelo Asaas.

O Asaas emite a fatura com PIX e boleto no mesmo link, e avisa por webhook
quando o dinheiro entra. É o que troca "eu confiro o extrato e marco na mão"
por "o acesso do restaurante volta sozinho quando ele paga".

Duas coisas que o código faz questão de tratar bem, porque são onde integração
de cobrança costuma doer:

1. **O cliente do Asaas é criado uma vez e reaproveitado.** O id fica no
   tenant. Criar um cliente novo a cada mês encheria a conta de duplicatas e
   quebraria os relatórios do próprio Asaas.
2. **A mensagem de erro dele chega inteira.** O Asaas diz coisas específicas
   ("O CPF/CNPJ informado é inválido"), e trocar isso por "erro ao cobrar"
   seria jogar fora a única informação útil.

Sem `requests`: só a biblioteca padrão, como no agente de impressão e no
provedor da Meta.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from flask import current_app

from .base import ProvedorCobranca, ResultadoCobranca

TEMPO_LIMITE = 25

# O sandbox é uma conta de testes completa e gratuita: emite fatura, aceita
# pagamento fictício e dispara webhook. Dá para exercitar o fluxo inteiro sem
# cobrar ninguém — e é por isso que ele é o padrão.
ENDERECOS = {
    "sandbox": "https://api-sandbox.asaas.com/v3",
    "producao": "https://api.asaas.com/v3",
}


def endereco_base() -> str:
    ambiente = (current_app.config.get("ASAAS_AMBIENTE") or "sandbox").strip().lower()
    return ENDERECOS.get(ambiente, ENDERECOS["sandbox"])


def em_producao() -> bool:
    return (current_app.config.get("ASAAS_AMBIENTE") or "").strip().lower() == "producao"


def _somente_digitos(valor: str) -> str:
    return "".join(caractere for caractere in (valor or "") if caractere.isdigit())


class Asaas(ProvedorCobranca):
    slug = "asaas"
    nome = "Asaas"
    automatico = True

    # ---------------------------------------------------------- configuração --
    def configurado(self) -> bool:
        return bool((current_app.config.get("ASAAS_API_KEY") or "").strip())

    def falta_configurar(self) -> str:
        if self.configurado():
            return ""
        return (
            "Falta a ASAAS_API_KEY no .env do servidor. Enquanto ela não existir, "
            "as mensalidades continuam sendo registradas na mão."
        )

    # ----------------------------------------------------------------- HTTP --
    def _chamar(self, caminho: str, corpo: dict | None = None, metodo: str = "POST") -> tuple[dict, str | None]:
        """Devolve (dados, erro). Só um dos dois vem preenchido."""
        requisicao = urllib.request.Request(
            f"{endereco_base()}{caminho}",
            data=json.dumps(corpo).encode("utf-8") if corpo is not None else None,
            headers={
                "access_token": (current_app.config.get("ASAAS_API_KEY") or "").strip(),
                "Content-Type": "application/json",
                "User-Agent": "ComandaAi/1.0",
            },
            method=metodo,
        )
        try:
            with urllib.request.urlopen(requisicao, timeout=TEMPO_LIMITE) as resposta:
                return json.loads(resposta.read().decode("utf-8")), None
        except urllib.error.HTTPError as exc:
            return {}, self._mensagem_de_erro(exc)
        except urllib.error.URLError as exc:
            return {}, f"Não foi possível falar com o Asaas: {exc.reason}"[:500]
        except (ValueError, json.JSONDecodeError):
            return {}, "O Asaas respondeu algo que não é JSON."

    @staticmethod
    def _mensagem_de_erro(exc: urllib.error.HTTPError) -> str:
        try:
            dados = json.loads(exc.read().decode("utf-8"))
        except Exception:
            return f"O Asaas respondeu com erro {exc.code}."
        # O formato do Asaas é {"errors": [{"code": ..., "description": ...}]}.
        descricoes = [
            str(erro.get("description") or "").strip()
            for erro in (dados.get("errors") or [])
            if erro.get("description")
        ]
        return ("; ".join(descricoes) or f"O Asaas respondeu com erro {exc.code}.")[:500]

    # -------------------------------------------------------------- cliente --
    def garantir_cliente(self, tenant) -> tuple[str | None, str | None]:
        """Id do restaurante dentro do Asaas, criando-o na primeira vez.

        Devolve (id, erro). O id fica gravado no tenant: criar um cliente novo a
        cada mês encheria a conta de duplicatas do mesmo restaurante.
        """
        if (tenant.asaas_customer_id or "").strip():
            return tenant.asaas_customer_id.strip(), None

        documento = _somente_digitos(tenant.cnpj or "")
        if len(documento) not in (11, 14):
            return None, (
                f"O restaurante “{tenant.nome_fantasia}” precisa de um CPF ou CNPJ "
                "cadastrado para ser cobrado pelo Asaas."
            )

        corpo = {
            "name": (tenant.razao_social or tenant.nome_fantasia)[:100],
            "cpfCnpj": documento,
            "email": tenant.email_contato,
            "externalReference": tenant.slug,
        }
        telefone = _somente_digitos(tenant.telefone_contato or "")
        if telefone:
            corpo["mobilePhone"] = telefone

        dados, erro = self._chamar("/customers", corpo)
        if erro:
            return None, erro
        identificador = dados.get("id")
        if not identificador:
            return None, "O Asaas não devolveu o identificador do cliente."

        from ...extensions import db

        tenant.asaas_customer_id = identificador
        db.session.commit()
        return identificador, None

    # ------------------------------------------------------------- cobrança --
    def criar(self, cobranca) -> ResultadoCobranca:
        if not self.configurado():
            return ResultadoCobranca(False, erro=self.falta_configurar())

        tenant = cobranca.tenant
        cliente, erro = self.garantir_cliente(tenant)
        if erro:
            return ResultadoCobranca(False, erro=erro)

        dados, erro = self._chamar(
            "/payments",
            {
                "customer": cliente,
                # UNDEFINED deixa o restaurante escolher entre PIX e boleto na
                # própria fatura, em vez de a plataforma escolher por ele.
                "billingType": "UNDEFINED",
                "value": float(cobranca.valor),
                "dueDate": cobranca.vencimento.isoformat(),
                "description": (
                    f"Comanda ai — mensalidade {cobranca.rotulo_competencia} "
                    f"({tenant.nome_fantasia})"
                )[:500],
                # É por aqui que o webhook reencontra a cobrança mesmo se o id
                # externo não tiver sido gravado (falha entre criar e commitar).
                "externalReference": f"cobranca:{cobranca.id}",
            },
        )
        if erro:
            return ResultadoCobranca(False, erro=erro)

        identificador = dados.get("id")
        if not identificador:
            return ResultadoCobranca(False, erro="O Asaas não devolveu o identificador da cobrança.")

        return ResultadoCobranca(
            True,
            id_externo=identificador,
            url_pagamento=dados.get("invoiceUrl") or dados.get("bankSlipUrl"),
            resposta=dados,
        )
