"""What to do for each Duepi alarm, and whether it may be reset remotely.

The advice is general guidance for Duepi EVO pellet stoves (Interstoves Diego 9); it must be checked
against the stove's manual. Severity 3 alarms are never reset from Home Assistant: they need an
inspection on site or a technician.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AlarmAdvice:
    advice: str
    resettable: bool


ADVICE: dict[int, AlarmAdvice] = {
    1: AlarmAdvice("Laisser refroidir. Vider le creuset des granulés non brûlés (ne jamais rallumer creuset plein), "
                   "vérifier le réservoir, nettoyer le creuset, puis acquitter : Netatmo relancera. "
                   "Deux échecs de suite : appeler le technicien (résistance d'allumage).", True),
    2: AlarmAdvice("Vérifier que la porte et le cendrier sont bien fermés, nettoyer le creuset et l'échangeur, "
                   "vérifier le conduit, puis acquitter. Si l'alarme revient : technicien.", True),
    3: AlarmAdvice("Creuset encrassé, porte mal fermée, prise d'air ou conduit obstrués, ou vent fort. "
                   "Nettoyer le poêle, puis acquitter.", True),
    4: AlarmAdvice("Sans objet sur un poêle à air : défaut de carte ou de sonde. Appeler le technicien.", False),
    5: AlarmAdvice("Réservoir vide : recharger, vider le creuset, acquitter. Netatmo relancera le poêle.", True),
    6: AlarmAdvice("Pressostat : conduit bouché, vent violent ou porte ouverte. Vérifier sur place ; "
                   "si l'alarme persiste, appeler le technicien.", False),
    8: AlarmAdvice("Coupure de courant pendant le fonctionnement : le poêle refroidit. Une fois éteint, acquitter ; "
                   "Netatmo relancera.", True),
    9: AlarmAdvice("Moteur d'extraction : ne pas relancer le poêle. Appeler le technicien.", False),
    10: AlarmAdvice("Surtension de la carte : couper l'alimentation du poêle et appeler le technicien.", False),
    11: AlarmAdvice("Date expirée : vérifier la date et l'heure du poêle, et l'échéance d'entretien.", True),
    13: AlarmAdvice("Sonde de régulation d'aspiration : appeler le technicien.", False),
    14: AlarmAdvice("Surchauffe : laisser refroidir, vérifier la ventilation et la vis ; réarmer le thermostat de "
                    "sécurité du poêle s'il a déclenché. Si l'alarme revient : technicien.", False),
}

UNKNOWN = AlarmAdvice("Code inconnu : noter le code affiché sur le poêle et consulter la notice. "
                      "Pas d'acquittement à distance.", False)


def advice_for(code: int) -> AlarmAdvice:
    return ADVICE.get(code, UNKNOWN)
