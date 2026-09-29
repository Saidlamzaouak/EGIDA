# -*- coding: utf-8 -*-
"""Migration 18.0.2.33.0 : ajout de planning.slot.shift_id.

Renseigne le shift des créneaux existants à partir des lignes Planning
Resources (même projet + même agent). Si l'agent a plusieurs lignes (plusieurs
shifts) sur le projet, on retient celle dont l'heure de début locale est la
plus proche de celle du créneau.
"""
import logging

import pytz

from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    Slot = env['planning.slot']
    total = 0
    lines = env['gs.project.planning.line'].search([
        ('employee_id', '!=', False), ('shift_id', '!=', False),
    ])
    lines_by_key = {}
    for line in lines:
        lines_by_key.setdefault(
            (line.project_id.id, line.employee_id.id), []).append(line)

    for (project_id, employee_id), key_lines in lines_by_key.items():
        slots = Slot.search([
            ('project_id', '=', project_id),
            ('employee_id', '=', employee_id),
            ('shift_id', '=', False),
            ('start_datetime', '!=', False),
        ])
        if not slots:
            continue
        project = key_lines[0].project_id
        tz = pytz.timezone(
            project.company_id.resource_calendar_id.tz or 'Africa/Casablanca')
        for slot in slots:
            line = key_lines[0]
            if len(key_lines) > 1:
                local = pytz.UTC.localize(slot.start_datetime).astimezone(tz)
                hour = local.hour + local.minute / 60.0
                line = min(key_lines, key=lambda l: abs(l.start_hour - hour))
            cr.execute(
                "UPDATE planning_slot SET shift_id = %s WHERE id = %s",
                (line.shift_id.id, slot.id),
            )
            total += 1
    _logger.info("Shift renseigné sur %d créneau(x) existant(s).", total)
