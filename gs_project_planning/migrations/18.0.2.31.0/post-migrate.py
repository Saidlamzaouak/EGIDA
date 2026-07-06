# -*- coding: utf-8 -*-
"""Migration 18.0.2.31.0 : les shifts absents ne comptent plus d'heures.

`duration_hours` est un champ stocké. Désormais un slot marqué absent a une
durée payée nulle (l'agent n'a pas travaillé — c'est son remplaçant qui porte
les heures sur son propre slot). Les slots absents déjà en base gardent leur
ancienne valeur (ex. 8 h) tant qu'ils ne sont pas recalculés : ce script force
la remise à 0 pour tous les slots absents existants.
"""
import logging

from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    if 'planning.slot' not in env:
        return

    absent_slots = env['planning.slot'].search([
        ('is_absent', '=', True),
        ('duration_hours', '!=', 0.0),
    ])
    if not absent_slots:
        _logger.info("gs_project_planning: aucun shift absent à recalculer.")
        return

    _logger.info(
        "gs_project_planning: remise à 0 des heures de %s shift(s) absent(s).",
        len(absent_slots),
    )
    absent_slots._compute_duration_hours()
