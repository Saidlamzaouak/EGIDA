# -*- coding: utf-8 -*-
"""Drop the CIN / CNSS uniqueness SQL constraints on hr_employee.

Doit tourner AVANT le chargement des nouveaux modèles et données, sinon le
chargement de sample_data / sample_contracts échoue sur des doublons légitimes
(ex. plusieurs agents avec cnss='RETRAITE').

Le nom des contraintes en base PostgreSQL est `<table>_<key>` :
  - hr_employee_uniq_cin
  - hr_employee_uniq_cnss
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    _logger.info("gs_project_planning 18.0.2.18.0 — drop uniq_cin/uniq_cnss")

    # 1) DROP CONSTRAINT côté PostgreSQL
    cr.execute("""
        ALTER TABLE hr_employee
            DROP CONSTRAINT IF EXISTS hr_employee_uniq_cin;
        ALTER TABLE hr_employee
            DROP CONSTRAINT IF EXISTS hr_employee_uniq_cnss;
    """)

    # 2) DROP INDEX si jamais l'unicité a laissé un index orphelin
    cr.execute("""
        DROP INDEX IF EXISTS hr_employee_uniq_cin;
        DROP INDEX IF EXISTS hr_employee_uniq_cnss;
    """)

    # 3) Purge des références dans ir_model_constraint pour que la réflexion
    #    Odoo ne tente pas de les recréer ni n'affiche d'avertissement.
    #    Le champ `name` y contient le nom PostgreSQL COMPLET, pas la clé.
    cr.execute("""
        DELETE FROM ir_model_constraint
         WHERE name IN ('hr_employee_uniq_cin', 'hr_employee_uniq_cnss');
    """)

    _logger.info("Contraintes CIN/CNSS supprimées avec succès.")
