# -*- coding: utf-8 -*-
"""Migration 18.0.2.2.0 : passage de shift_type (Selection) à shift_id (M2O).

Pour chaque ligne planning existante, on cherche le shift correspondant
via le code technique (= ancienne valeur de shift_type) et on met à jour
shift_id.

Les xmlids cibles sont fournis par data/gs_planning_shift_data.xml qui
est chargé AVANT cette migration grâce à l'ordre dans le manifest.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    # Vérifier que l'ancienne colonne shift_type existe encore
    cr.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'gs_project_planning_line'
          AND column_name = 'shift_type'
    """)
    if not cr.fetchone():
        _logger.info("shift_type column already removed, migration skipped.")
        return

    # Mapper ancien code (security_morning) au shift_id via ir_model_data
    cr.execute("""
        UPDATE gs_project_planning_line line
        SET shift_id = imd.res_id
        FROM ir_model_data imd
        WHERE imd.module = 'gs_project_planning'
          AND imd.model = 'gs.planning.shift'
          AND imd.name = 'shift_' || line.shift_type
          AND line.shift_id IS NULL
    """)
    updated = cr.rowcount
    _logger.info(
        "Migrated %d planning line(s) from shift_type to shift_id.",
        updated,
    )

    # Vérifier qu'il ne reste pas de NULL (sinon required=True échouera)
    cr.execute("""
        SELECT id, shift_type FROM gs_project_planning_line
        WHERE shift_id IS NULL
    """)
    orphans = cr.fetchall()
    if orphans:
        _logger.warning(
            "%d planning lines have NULL shift_id after migration: %s",
            len(orphans), orphans,
        )
