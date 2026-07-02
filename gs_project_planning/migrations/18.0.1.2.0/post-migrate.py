# -*- coding: utf-8 -*-
"""Migration 18.0.1.2.0 : passage du daily_hour_limit en mode auto/manuel.

Les projets existants avec un daily_hour_limit > 0 ont une valeur que
l'utilisateur a saisie (ou que l'onchange historique a posée). Pour ne
PAS l'écraser au prochain changement d'équipe, on les bascule en mode
manuel (daily_hour_limit_auto = False).

Les nouveaux projets utiliseront le mode auto (default=True du champ).
"""


def migrate(cr, version):
    cr.execute("""
        UPDATE project_project
        SET daily_hour_limit_auto = FALSE
        WHERE daily_hour_limit > 0
    """)
