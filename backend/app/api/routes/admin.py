"""Rotas administrativas — painel de disparo de busca.

Toda rota aqui usa ``require_admin``, nunca ``get_current_user``: um usuário
"client" autenticado recebe **403** (não 401 — ele tem token válido, só não
tem permissão) e não alcança o painel nem digitando a URL.

⚠️ **``POST /buscas`` gasta dinheiro real.** Ver
``app/workers/execucao_busca.py`` pro que roda depois do 201.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.core.database import get_db
from app.core.segredos import erro_redigido
from app.core.tempo import agora_utc
from app.models.busca_leads import BuscaLeadsRegistro, StatusBusca
from app.models.user import User
from app.schemas.busca_leads import BuscaLeadsRead

logger = logging.getLogger(__name__)

router = APIRouter()


def get_disparador() -> Callable[[str], None]:
    """Devolve a função que despacha a busca pro worker.

    É uma **dependência do FastAPI**, não um import direto, por dois motivos
    concretos:

    1. **A suíte nunca toca no Celery.** Os testes trocam isto por um fake via
       ``app.dependency_overrides`` e exercitam a rota inteira sem broker,
       sem worker e — o que importa — sem nenhum caminho que gaste dinheiro.
    2. **O processo da API não precisa do Celery pra subir.** O import fica
       dentro da função: se o broker estiver mal configurado, quem descobre é
       quem dispara uma busca, não quem sobe o uvicorn.
    """
    from app.workers.celery_app import task_executar_busca_completa

    def _disparar(busca_id: str) -> None:
        task_executar_busca_completa.delay(busca_id)

    return _disparar


@router.post(
    "/buscas", response_model=BuscaLeadsRead, status_code=status.HTTP_201_CREATED
)
def disparar_busca(
    db: Session = Depends(get_db),
    usuario: User = Depends(require_admin),
    disparar: Callable[[str], None] = Depends(get_disparador),
) -> BuscaLeadsRegistro:
    """Dispara uma nova busca — **custo real de API**, não simulação.

    Cria o registro com ``status="executando"``, despacha pro worker e devolve
    o registro **imediatamente**. A execução leva minutos (só a leitura do
    Sicor já demora, e depois vem o enriquecimento pago de ~60 leads), então
    nada disso acontece dentro do request: o frontend acompanha por polling em
    ``GET /buscas/{id}``.

    **409** se já houver uma busca "executando". É trava contra clique duplo /
    dois admins na mesma tela — checagem best-effort, não lock de banco
    (``SELECT ... FOR UPDATE``): não previne duas requisições no mesmo
    instante, e não pretende. O caso real é o clique duplo.

    **503** se o despacho falhar (broker fora do ar). Neste caso o registro
    recém-criado é marcado como "erro" antes de responder — sem isso ele
    ficaria "executando" para sempre, e a trava de 409 acima travaria o painel
    permanentemente por causa de uma busca que nunca começou.
    """
    em_andamento = db.execute(
        select(BuscaLeadsRegistro).where(
            BuscaLeadsRegistro.status == StatusBusca.EXECUTANDO.value
        )
    ).scalars().first()
    if em_andamento is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Já existe uma busca em andamento, iniciada em "
                f"{em_andamento.iniciado_em.strftime('%d/%m/%Y %H:%M')}."
            ),
        )

    registro = BuscaLeadsRegistro(
        iniciado_por_id=usuario.id, status=StatusBusca.EXECUTANDO.value
    )
    db.add(registro)
    db.commit()
    db.refresh(registro)

    try:
        disparar(registro.id)
    except Exception as exc:  # noqa: BLE001 — não deixar registro órfão
        registro.status = StatusBusca.ERRO.value
        registro.erros = [f"falha ao despachar a busca: {erro_redigido(exc)}"]
        registro.concluido_em = agora_utc()
        db.commit()
        logger.error(
            "busca %s criada mas não despachada (worker/broker indisponível): %s",
            registro.id, erro_redigido(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Não foi possível iniciar a busca: o processador de tarefas "
                "está indisponível. Nenhum custo foi gerado."
            ),
        ) from exc

    logger.warning(
        "busca %s disparada por %s — enriquecimento PAGO vai rodar no worker.",
        registro.id, usuario.email,
    )
    return registro


@router.get("/buscas", response_model=list[BuscaLeadsRead])
def listar_buscas(
    db: Session = Depends(get_db),
    _usuario: User = Depends(require_admin),
    limit: int = Query(default=20, ge=1, le=100),
) -> list[BuscaLeadsRegistro]:
    """Histórico de buscas, mais recente primeiro."""
    consulta = (
        select(BuscaLeadsRegistro)
        .order_by(BuscaLeadsRegistro.iniciado_em.desc())
        .limit(limit)
    )
    return list(db.execute(consulta).scalars().all())


@router.get("/buscas/{busca_id}", response_model=BuscaLeadsRead)
def obter_busca(
    busca_id: str,
    db: Session = Depends(get_db),
    _usuario: User = Depends(require_admin),
) -> BuscaLeadsRegistro:
    """Estado atual de uma busca — é o que o frontend consulta em polling
    enquanto ``status == "executando"``."""
    registro = db.get(BuscaLeadsRegistro, busca_id)
    if registro is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Busca não encontrada"
        )
    return registro
