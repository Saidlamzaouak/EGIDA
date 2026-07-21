# -*- coding: utf-8 -*-
"""Migration 18.0.2.40.0 : jour de repos unique -> jours de repos multiples.

`rest_weekday` (Selection '0'..'6', un seul jour) est remplacé par
`rest_weekday_ids` (Many2many vers gs.rest.weekday), afin de pouvoir déclarer
plusieurs jours de repos (ex. Samedi + Dimanche).

Ce script recopie les valeurs déjà saisies (sur les fiches employés et sur les
lignes Planning Resources) vers la nouvelle relation. L'ancienne colonne est
conservée en base (non utilisée) par sécurité.
"""
import logging

from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)

# (table SQL, modèle ORM, champ Many2many cible)
_TARGETS = [
    ('hr_employee', 'hr.employee', 'rest_weekday_ids'),
    ('gs_project_planning_line', 'gs.project.planning.line', 'rest_weekday_ids'),
]


def _column_exists(cr, table, column):
    cr.execute("""
        SELECT 1 FROM information_schema.columns
        WHERE table_name = %s AND column_name = %s
    """, (table, column))
    return bool(cr.fetchone())


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    if 'gs.rest.weekday' not in env:
        return

    # dayofweek (0..6) -> id du référentiel
    weekday_by_dow = {
        w.dayofweek: w.id for w in env['gs.rest.weekday'].search([])
    }
    if not weekday_by_dow:
        _logger.warning(
            "gs_project_planning: référentiel gs.rest.weekday vide, "
            "migration des jours de repos ignorée."
        )
        return

    for table, model_name, m2m_field in _TARGETS:
        if model_name not in env:
            continue
        if not _column_exists(cr, table, 'rest_weekday'):
            continue

        cr.execute(
            "SELECT id, rest_weekday FROM {} "
            "WHERE rest_weekday IS NOT NULL AND rest_weekday != ''".format(table)
        )
        rows = cr.fetchall()
        if not rows:
            continue

        migrated = 0
        for res_id, raw_value in rows:
            try:
                dow = int(raw_value)
            except (TypeError, ValueError):
                continue
            weekday_id = weekday_by_dow.get(dow)
            if not weekday_id:
                continue
            record = env[model_name].browse(res_id)
            if not record.exists():
                continue
            record.write({m2m_field: [(4, weekday_id)]})
            migrated += 1

        _logger.info(
            "gs_project_planning: %s jour(s) de repos migré(s) sur %s.",
            migrated, model_name,
        )
