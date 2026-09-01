# ============================================================
# PANEL_RENDIMIENTO_OJO99.py
# Panel persistente de rendimiento para OJO-99 Omega.
#
# - Guarda predicciones y resultados en PostgreSQL/SQLite.
# - No inventa porcentajes.
# - Permite medir Top 5, palés, tripletas y Jugada Maestra.
# ============================================================

from datetime import datetime, timezone
from pathlib import Path
import json
import os

from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    DateTime,
    Text,
    Boolean,
    UniqueConstraint,
    select,
)
from sqlalchemy.orm import declarative_base, sessionmaker


DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/ojo99.db")

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace(
        "postgres://", "postgresql+psycopg://", 1
    )
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace(
        "postgresql://", "postgresql+psycopg://", 1
    )

if DATABASE_URL.startswith("sqlite"):
    Path("./data").mkdir(parents=True, exist_ok=True)

connect_args = (
    {"check_same_thread": False}
    if DATABASE_URL.startswith("sqlite")
    else {}
)

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    connect_args=connect_args,
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
)

Base = declarative_base()


class PredictionRecord(Base):
    __tablename__ = "ojo99_predictions"

    id = Column(Integer, primary_key=True)
    lottery = Column(String(160), nullable=False, index=True)
    draw_time = Column(DateTime(timezone=True), nullable=False, index=True)

    top5_numbers_json = Column(Text, nullable=False, default="[]")
    top5_pairs_json = Column(Text, nullable=False, default="[]")
    top5_triples_json = Column(Text, nullable=False, default="[]")
    master_pair_json = Column(Text, nullable=True)

    result_json = Column(Text, nullable=True)

    evaluated = Column(Boolean, default=False, index=True)

    hit_top5_numbers = Column(Boolean, default=False)
    hit_top5_pairs = Column(Boolean, default=False)
    hit_top5_triples = Column(Boolean, default=False)
    hit_master = Column(Boolean, default=False)

    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        UniqueConstraint(
            "lottery",
            "draw_time",
            name="uq_ojo99_prediction_lottery_draw",
        ),
    )


Base.metadata.create_all(engine)


def _normalize_num(value):
    return int(value)


def _normalize_pair(pair):
    return sorted([int(pair[0]), int(pair[1])])


def _normalize_triple(triple):
    return sorted([int(x) for x in triple])


class OJO99Performance:
    def guardar_prediccion(
        self,
        lottery,
        draw_time,
        top5_numeros=None,
        top5_pales=None,
        top5_tripletas=None,
        jugada_maestra=None,
    ):
        top5_numeros = top5_numeros or []
        top5_pales = top5_pales or []
        top5_tripletas = top5_tripletas or []

        if draw_time.tzinfo is None:
            draw_time = draw_time.replace(tzinfo=timezone.utc)

        with SessionLocal() as db:
            existing = db.scalar(
                select(PredictionRecord).where(
                    PredictionRecord.lottery == lottery,
                    PredictionRecord.draw_time == draw_time,
                )
            )

            if existing:
                return {
                    "ok": False,
                    "reason": "PREDICCION_YA_GUARDADA",
                    "id": existing.id,
                }

            row = PredictionRecord(
                lottery=lottery,
                draw_time=draw_time,
                top5_numbers_json=json.dumps(
                    [int(x) for x in top5_numeros]
                ),
                top5_pairs_json=json.dumps(
                    [[int(a), int(b)] for a, b in top5_pales]
                ),
                top5_triples_json=json.dumps(
                    [[int(x) for x in t] for t in top5_tripletas]
                ),
                master_pair_json=(
                    json.dumps(
                        [
                            int(jugada_maestra[0]),
                            int(jugada_maestra[1]),
                        ]
                    )
                    if jugada_maestra
                    else None
                ),
            )

            db.add(row)
            db.commit()
            db.refresh(row)

            return {
                "ok": True,
                "id": row.id,
            }

    def evaluar_resultado(
        self,
        lottery,
        draw_time,
        resultado,
    ):
        if draw_time.tzinfo is None:
            draw_time = draw_time.replace(tzinfo=timezone.utc)

        resultado = [int(x) for x in resultado]
        resultado_set = set(resultado)

        with SessionLocal() as db:
            row = db.scalar(
                select(PredictionRecord).where(
                    PredictionRecord.lottery == lottery,
                    PredictionRecord.draw_time == draw_time,
                )
            )

            if not row:
                return {
                    "ok": False,
                    "reason": "SIN_PREDICCION_PREVIA",
                }

            if row.evaluated:
                return {
                    "ok": False,
                    "reason": "YA_EVALUADA",
                    "id": row.id,
                }

            top5_numbers = json.loads(row.top5_numbers_json or "[]")
            top5_pairs = json.loads(row.top5_pairs_json or "[]")
            top5_triples = json.loads(row.top5_triples_json or "[]")
            master_pair = (
                json.loads(row.master_pair_json)
                if row.master_pair_json
                else None
            )

            hit_numbers = any(
                int(n) in resultado_set
                for n in top5_numbers
            )

            hit_pairs = any(
                set(_normalize_pair(pair)).issubset(resultado_set)
                for pair in top5_pairs
                if len(pair) == 2
            )

            hit_triples = any(
                set(_normalize_triple(triple)).issubset(resultado_set)
                for triple in top5_triples
                if len(triple) == 3
            )

            hit_master = False
            if master_pair and len(master_pair) == 2:
                hit_master = set(
                    _normalize_pair(master_pair)
                ).issubset(resultado_set)

            row.result_json = json.dumps(resultado)
            row.evaluated = True
            row.hit_top5_numbers = hit_numbers
            row.hit_top5_pairs = hit_pairs
            row.hit_top5_triples = hit_triples
            row.hit_master = hit_master

            db.commit()

            return {
                "ok": True,
                "top5_numeros": hit_numbers,
                "top5_pales": hit_pairs,
                "top5_tripletas": hit_triples,
                "jugada_maestra": hit_master,
            }

    def resumen(self, lottery):
        with SessionLocal() as db:
            rows = db.scalars(
                select(PredictionRecord).where(
                    PredictionRecord.lottery == lottery,
                    PredictionRecord.evaluated == True,  # noqa: E712
                )
            ).all()

        total = len(rows)

        if total < 30:
            estado = "MUESTRA INSUFICIENTE"
        elif total < 100:
            estado = "MUESTRA EN DESARROLLO"
        else:
            estado = "MUESTRA UTILIZABLE"

        def pct(n, d):
            if d <= 0:
                return None
            return round((n / d) * 100, 2)

        hit_numbers = sum(1 for r in rows if r.hit_top5_numbers)
        hit_pairs = sum(1 for r in rows if r.hit_top5_pairs)
        hit_triples = sum(1 for r in rows if r.hit_top5_triples)

        master_rows = [
            r for r in rows
            if r.master_pair_json
        ]
        master_total = len(master_rows)
        master_hits = sum(
            1 for r in master_rows
            if r.hit_master
        )

        return {
            "lottery": lottery,
            "estado": estado,
            "predicciones_evaluadas": total,
            "top5_numeros": {
                "aciertos": hit_numbers,
                "porcentaje": pct(hit_numbers, total),
            },
            "top5_pales": {
                "aciertos": hit_pairs,
                "porcentaje": pct(hit_pairs, total),
            },
            "top5_tripletas": {
                "aciertos": hit_triples,
                "porcentaje": pct(hit_triples, total),
            },
            "jugada_maestra": {
                "alertas_emitidas": master_total,
                "aciertos": master_hits,
                "porcentaje": pct(master_hits, master_total),
            },
        }

    # Compatibilidad con el código anterior:
    def registrar_resultado(
        self,
        lottery,
        resultado,
        top5_numeros=None,
        top5_pales=None,
        top5_tripletas=None,
        jugada_maestra=None,
    ):
        """
        Método legado.
        Para rendimiento real se recomienda usar:
        guardar_prediccion(...) ANTES del sorteo
        y evaluar_resultado(...) DESPUÉS del sorteo.
        """
        return {
            "ok": False,
            "reason": "USA_GUARDAR_PREDICCION_Y_EVALUAR_RESULTADO",
        }
