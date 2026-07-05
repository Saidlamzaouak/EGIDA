# -*- coding: utf-8 -*-
"""Migration 18.0.2.26.0 : purge des données d'exemple.

Les fichiers data/sample_data.xml et data/sample_contracts.xml ont été
retirés du manifest (ils créaient 13 employés, 5 projets, 20 lignes de
planning et 13 contrats FICTIFS dans TOUTE base, y compris production).

Ces enregistrements portent l'attribut noupdate="1" : Odoo ne les nettoie
donc PAS automatiquement lors du -u. Ce script les supprime explicitement,
en ciblant leur xmlid (tous préfixés « demo_ » dans le module).

La config structurelle (rôles, stages, shifts — préfixes différents) n'est
PAS concernée : le module continue de fonctionner.

IMPORTANT : chaque suppression est encadrée d'un SAVEPOINT. En PostgreSQL,
dès qu'une requête échoue (ex. une FK depuis un enregistrement hors
échantillon), toute la transaction est gelée jusqu'à un rollback. Le
savepoint permet de rollback UNIQUEMENT la suppression fautive, de logguer
l'enregistrement à traiter manuellement, et de poursuivre la purge.
"""
import logging
from collections import defaultdict

from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)

# Ordre de suppression (respecte les FK : les lignes/contrats référencent
# les employés en ondelete='restrict', il faut donc les supprimer AVANT).
DELETE_ORDER = [
    'gs.project.planning.line',
    'hr.contract',
    'project.project',
    'hr.employee',
]


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})

    data_recs = env['ir.model.data'].search([
        ('module', '=', 'gs_project_planning'),
        ('name', '=like', 'demo_%'),
    ])
    if not data_recs:
        _logger.info("gs_project_planning: aucune donnée d'exemple à purger.")
        return

    by_model = defaultdict(list)
    for d in data_recs:
        by_model[d.model].append(d.res_id)

    total = sum(len(v) for v in by_model.values())
    _logger.info("gs_project_planning: purge de %s enregistrement(s) "
                 "d'exemple sur %s modèle(s).", total, len(by_model))

    deleted = 0
    failed = 0

    # 1) Supprimer d'abord les créneaux générés pour les projets d'exemple,
    #    sinon la suppression des projets peut être bloquée.
    demo_project_ids = by_model.get('project.project', [])
    if demo_project_ids and 'planning.slot' in env:
        slots = env['planning.slot'].search(
            [('project_id', 'in', demo_project_ids)])
        if slots:
            d, f = _safe_unlink(env, slots)
            _logger.info("  → planning.slot : %s supprimé(s), %s en échec.", d, f)
            deleted += d
            failed += f

    # 2) Supprimer les enregistrements d'exemple dans l'ordre des dépendances.
    ordered = DELETE_ORDER + [m for m in by_model if m not in DELETE_ORDER]
    for model in ordered:
        res_ids = by_model.get(model)
        if not res_ids or model not in env:
            continue
        recs = _browse_existing(env, model, res_ids)
        if not recs:
            continue
        d, f = _safe_unlink(env, recs)
        _logger.info("  → %s : %s supprimé(s), %s en échec.", model, d, f)
        deleted += d
        failed += f

    _logger.info("gs_project_planning: purge terminée — %s supprimé(s), "
                 "%s à traiter manuellement.", deleted, failed)


def _browse_existing(env, model, res_ids):
    """.exists() protégé par un savepoint (au cas où la transaction soit
    fragilisée par une étape précédente)."""
    try:
        with env.cr.savepoint():
            return env[model].browse(res_ids).exists()
    except Exception as exc:  # pragma: no cover
        _logger.warning("Lecture %s impossible : %s", model, exc)
        env.invalidate_all()
        return env[model].browse([])


def _safe_unlink(env, records):
    """Unlink robuste : tente en lot dans un savepoint, retombe sur du
    un-par-un (chacun dans son propre savepoint) en cas de blocage.

    Retourne (nb_supprimés, nb_échecs)."""
    try:
        with env.cr.savepoint():
            records.unlink()
        return len(records), 0
    except Exception as exc:
        env.invalidate_all()
        _logger.warning("Suppression en lot échouée (%s). Reprise unitaire.", exc)

    ok = ko = 0
    for rec in records:
        try:
            with env.cr.savepoint():
                rec.unlink()
            ok += 1
        except Exception as exc:  # pragma: no cover
            env.invalidate_all()
            ko += 1
            _logger.warning(
                "Impossible de supprimer %s (id=%s) : %s. "
                "Enregistrement d'exemple à supprimer manuellement.",
                rec._name, rec.id, exc)
    return ok, ko
