"""Teste manual do enriquecimento pago contra POUCOS leads reais.

⚠️ **ESTE SCRIPT GASTA DINHEIRO DE VERDADE.** Cada execução consome:

  - API Full        1 consulta por CPF processado   (pré-pago)
  - BrasilAPI       1 consulta por CNPJ             (gratuita)
  - Evolution       1 consulta por lead com telefone (infra própria)
  - Firecrawl       1 scrape por lead COM domínio próprio (raro — ver abaixo)
  - Hunter.io       1 consulta por lead com domínio E sem e-mail conhecido
  - ZeroBounce      1 consulta por e-mail que passou no MX
  - Anthropic       1 chamada por lead cujo site foi lido com sucesso

Não é chamado por nenhum teste automatizado, e não deve ser. A fixture
``autouse`` do ``conftest.py`` bloqueia socket na suíte inteira; este script
roda **fora** dela, direto pelo interpretador.

Uso::

    .venv/bin/python scripts/teste_real_enriquecimento.py            # 3 leads
    .venv/bin/python scripts/teste_real_enriquecimento.py --leads 1  # 1 só
    .venv/bin/python scripts/teste_real_enriquecimento.py --simular  # NÃO gasta

``--simular`` faz tudo (lê semente, pré-seleciona, escolhe os leads, imprime
quais seriam processados) e **para antes de qualquer chamada paga**. Serve
pra conferir a seleção sem custo.

O limite de volume vive AQUI, não na assinatura de produção: mudar
``enriquecer_selecionados`` só por causa de um teste manual seria carregar
pro código de produção uma preocupação que é desta sessão.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings  # noqa: E402
from app.core.documentos import TIPO_CNPJ, TIPO_CPF, detectar_tipo_documento  # noqa: E402
from app.scoring.pre_selecao import ORIGEM_SICOR, pre_selecionar  # noqa: E402
from app.services.receita_federal import CNAES_AGRO_TODOS, buscar_semente_cnpj  # noqa: E402
from app.services.sicor import extrair_leads_sicor  # noqa: E402
from app.workers.busca import enriquecer_selecionados  # noqa: E402

DIR_SICOR = Path("dados_locais/sicor")
DIR_RFB = Path("tests/dados_teste/rfb_amostra")
ANOS = [2025, 2026]
UF = "PR"


def mascarar(documento: str) -> str:
    """CPF/CNPJ parcialmente oculto — o log fica legível sem expor o titular."""
    if len(documento) < 6:
        return documento
    return f"{documento[:3]}***{documento[-2:]}"


def selecionar_do_ranking(candidatos: list, quantos: int) -> list:
    """Os ``quantos`` primeiros do ranking, **estritamente nessa ordem**.

    ⚠️ Sem regra especial de composição. A versão anterior tinha uma:

        if cnpjs and quantos >= 2:      # ← o bug
            escolhidos.append(cnpjs[0])

    A intenção era "se couber CNPJ e CPF, traga os dois". O efeito real era
    que ``--leads 1`` **nunca** incluía o CNPJ, mesmo sendo ele o primeiro do
    ranking — e ``--leads 1`` era justamente o comando recomendado pra testar
    barato, porque CNPJ vai pra BrasilAPI (gratuita) e CPF vai pra API Full
    (paga). O resultado foi gastar um crédito real numa rodada que deveria
    ser gratuita.

    A lição não é "escrever a guarda direito", é **não ter guarda**: o script
    respeita o ranking da pré-seleção, que já é a ordem de prioridade
    acordada. Compor a amostra por tipo de documento seria reintroduzir a
    mesma classe de erro.
    """
    return list(candidatos[:quantos])


def escolher_leads(quantos: int) -> list:
    """Pré-seleciona e devolve os ``quantos`` primeiros do ranking."""
    print(f"lendo semente Sicor ({UF}, {ANOS})… isso leva alguns minutos")
    sicor = extrair_leads_sicor(DIR_SICOR, uf=UF, anos=ANOS)
    print(f"  {len(sicor.leads):,} produtores")
    rfb = buscar_semente_cnpj(DIR_RFB, cnaes=CNAES_AGRO_TODOS, ufs={UF})
    print(f"  {len(rfb.estabelecimentos):,} estabelecimentos da Receita")

    pre = pre_selecionar(sicor.leads, rfb.estabelecimentos, cota=0)
    do_sicor = [c for c in pre.selecionados if c.origem == ORIGEM_SICOR]
    cpfs = sum(1 for c in do_sicor if detectar_tipo_documento(c.documento) == TIPO_CPF)
    print(f"  no universo pré-selecionado: {cpfs:,} CPF, {len(do_sicor) - cpfs:,} CNPJ")
    return selecionar_do_ranking(do_sicor, quantos)


def relatar(i: int, lead) -> None:
    c = lead.candidato
    tipo = detectar_tipo_documento(c.documento)
    d = c.dados_nicho
    print(f"\n{'=' * 62}")
    print(f"LEAD {i} — {mascarar(c.documento)} ({tipo}) — {d.get('area_ha')} ha")
    print(f"{'=' * 62}")
    print(f"  decisor      : {'SIM' if lead.decisor_identificavel else 'NAO':4} "
          f"| fonte={lead.fonte_decisor or '-'} | nome={lead.nome or '-'}")
    print(f"  site         : {'SIM' if lead.site_url else 'NAO':4} | {lead.site_url or '-'}")
    print(f"  instagram    : {lead.instagram or '-'}")
    print(f"  whatsapp     : {'SIM' if lead.tem_whatsapp else 'NAO':4} "
          f"| numero={lead.whatsapp_numero or '-'}")
    print(f"  e-mail       : {'APROVADO' if lead.email_aprovado else 'NAO':8} "
          f"| status={lead.email_status or '-'} | {len(lead.emails)} encontrado(s)")
    print(f"  presenca dig.: {lead.presenca_digital:.2f}")
    print(f"  SCORE        : {lead.score} ({lead.prioridade})")
    if lead.etapas_puladas:
        print("  etapas puladas:")
        for e in lead.etapas_puladas:
            print(f"    - {e['etapa']}: {e['motivo']}")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--leads", type=int, default=3, help="quantos leads (padrão 3)")
    p.add_argument("--simular", action="store_true",
                   help="escolhe os leads e PARA antes de gastar")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    print("=== guardas de configuração ===")
    guardas = {
        "API Full (CPF, PAGA)": settings.api_full_configurada,
        "Evolution (WhatsApp)": settings.evolution_configurada,
        "Firecrawl (site)": settings.firecrawl_configurada,
        "Hunter.io (e-mail)": settings.hunter_configurada,
        "ZeroBounce (e-mail)": settings.zerobounce_configurada,
        "Anthropic (IA)": settings.anthropic_configurada,
    }
    for nome, ok in guardas.items():
        print(f"  {nome:24} {'ok' if ok else 'AUSENTE — etapa será pulada'}")

    escolhidos = escolher_leads(args.leads)
    if not escolhidos:
        print("\nnenhum lead selecionado — abortando")
        return 1

    print(f"\n=== {len(escolhidos)} lead(s) escolhido(s) ===")
    for c in escolhidos:
        print(f"  {mascarar(c.documento)} ({detectar_tipo_documento(c.documento)}) "
              f"area={c.dados_nicho.get('area_ha')} ha "
              f"data={c.dados_nicho.get('data_operacao')} "
              f"recorrente={c.dados_nicho.get('recorrente')}")

    if args.simular:
        print("\n--simular: parando ANTES de qualquer chamada paga. Nada foi gasto.")
        return 0

    n_cpf = sum(1 for c in escolhidos if detectar_tipo_documento(c.documento) == TIPO_CPF)
    print(f"\n⚠️  A PARTIR DAQUI GASTA: até {n_cpf} consulta(s) API Full, "
          f"{len(escolhidos)} Evolution, e Firecrawl/Hunter/ZeroBounce/IA "
          f"conforme cada lead tiver domínio e site.")
    print("Enriquecendo…\n")

    enriquecidos = enriquecer_selecionados(escolhidos, apenas_decisor=False)
    for i, lead in enumerate(enriquecidos, 1):
        relatar(i, lead)

    print(f"\n{'=' * 62}")
    print("RESUMO")
    print(f"{'=' * 62}")
    print(f"  com decisor         : {sum(1 for x in enriquecidos if x.decisor_identificavel)}/{len(enriquecidos)}")
    print(f"  com WhatsApp ativo  : {sum(1 for x in enriquecidos if x.tem_whatsapp)}/{len(enriquecidos)}")
    print(f"  com e-mail aprovado : {sum(1 for x in enriquecidos if x.email_aprovado)}/{len(enriquecidos)}")
    print(f"  com site lido       : {sum(1 for x in enriquecidos if x.site_url)}/{len(enriquecidos)}")
    print(f"  presença digital > 0: {sum(1 for x in enriquecidos if x.presenca_digital > 0)}/{len(enriquecidos)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
