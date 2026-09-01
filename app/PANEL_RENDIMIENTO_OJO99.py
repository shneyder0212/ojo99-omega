# ============================================================
# PANEL DE RENDIMIENTO OJO-99 OMEGA
# Mide resultados reales. No inventa porcentajes.
# ============================================================

from collections import defaultdict

class OJO99Performance:
    def __init__(self):
        self.stats = defaultdict(lambda: {
            "predicciones": 0,
            "aciertos_top5_numeros": 0,
            "aciertos_top5_pales": 0,
            "aciertos_top5_tripletas": 0,
            "jugadas_maestras": 0,
            "aciertos_jugada_maestra": 0
        })

    def registrar_resultado(
        self,
        lottery,
        resultado,
        top5_numeros=None,
        top5_pales=None,
        top5_tripletas=None,
        jugada_maestra=None
    ):
        top5_numeros = top5_numeros or []
        top5_pales = top5_pales or []
        top5_tripletas = top5_tripletas or []

        resultado = [int(x) for x in resultado]
        resultado_set = set(resultado)

        s = self.stats[lottery]
        s["predicciones"] += 1

        # TOP 5 NÚMEROS
        if any(int(n) in resultado_set for n in top5_numeros):
            s["aciertos_top5_numeros"] += 1

        # TOP 5 PALÉS
        for pale in top5_pales:
            p = {int(pale[0]), int(pale[1])}
            if p.issubset(resultado_set):
                s["aciertos_top5_pales"] += 1
                break

        # TOP 5 TRIPLETAS
        for tripleta in top5_tripletas:
            t = {int(x) for x in tripleta}
            if t.issubset(resultado_set):
                s["aciertos_top5_tripletas"] += 1
                break

        # JUGADA MAESTRA
        if jugada_maestra:
            s["jugadas_maestras"] += 1
            jm = {int(jugada_maestra[0]), int(jugada_maestra[1])}

            if jm.issubset(resultado_set):
                s["aciertos_jugada_maestra"] += 1

    def porcentaje(self, aciertos, total):
        if total <= 0:
            return None

        return round((aciertos / total) * 100, 2)

    def resumen(self, lottery):
        s = self.stats[lottery]

        if s["predicciones"] < 30:
            estado = "MUESTRA INSUFICIENTE"
        elif s["predicciones"] < 100:
            estado = "MUESTRA EN DESARROLLO"
        else:
            estado = "MUESTRA UTILIZABLE"

        return {
            "lottery": lottery,
            "estado": estado,
            "predicciones_evaluadas": s["predicciones"],

            "top5_numeros": {
                "aciertos": s["aciertos_top5_numeros"],
                "porcentaje": self.porcentaje(
                    s["aciertos_top5_numeros"],
                    s["predicciones"]
                )
            },

            "top5_pales": {
                "aciertos": s["aciertos_top5_pales"],
                "porcentaje": self.porcentaje(
                    s["aciertos_top5_pales"],
                    s["predicciones"]
                )
            },

            "top5_tripletas": {
                "aciertos": s["aciertos_top5_tripletas"],
                "porcentaje": self.porcentaje(
                    s["aciertos_top5_tripletas"],
                    s["predicciones"]
                )
            },

            "jugada_maestra": {
                "alertas_emitidas": s["jugadas_maestras"],
                "aciertos": s["aciertos_jugada_maestra"],
                "porcentaje": self.porcentaje(
                    s["aciertos_jugada_maestra"],
                    s["jugadas_maestras"]
                )
            }
        }
