# -*- coding: utf-8 -*-
from odoo import models, fields, api, _


class GsPlanningShift(models.Model):
    _name = 'gs.planning.shift'
    _description = "Type de shift configurable (catalogue de plages horaires)"
    _order = 'sequence, name'
    _rec_name = 'display_name'

    name = fields.Char(string="Nom", required=True, translate=True)
    code = fields.Char(
        string="Code technique", required=True, copy=False,
        help="Identifiant unique en snake_case (ex. security_morning).",
    )
    sequence = fields.Integer(default=10)
    start_hour = fields.Float(string="Heure de début", required=True)
    end_hour = fields.Float(string="Heure de fin", required=True)
    crosses_midnight = fields.Boolean(
        string="Passe minuit",
        compute='_compute_crosses_midnight', store=True,
        help="Vrai automatiquement si heure_fin <= heure_début "
             "(ex. 22h → 06h le lendemain).",
    )
    duration = fields.Float(
        string="Durée (h)",
        compute='_compute_duration', store=True,
    )
    role_id = fields.Many2one(
        'planning.role', string="Rôle par défaut",
        help="Rôle préselectionné quand cette plage est choisie sur une ligne de planning.",
    )
    break_start_hour = fields.Float(
        string="Début pause", default=0.0,
        help="Heure de début de pause par défaut (ex. 13.0 pour 13h). "
             "0 = pas de pause configurée par défaut.",
    )
    break_end_hour = fields.Float(
        string="Fin pause", default=0.0,
        help="Heure de fin de pause par défaut (ex. 15.0 pour 15h).",
    )
    break_duration = fields.Float(
        string="Durée pause (h)",
        compute='_compute_break_duration', store=True,
        help="Durée de pause calculée = fin - début si valide, sinon 0.",
    )
    paid_duration = fields.Float(
        string="Heures payées (h)",
        compute='_compute_paid_duration', store=True,
        help="Durée du shift hors pause = duration - break_duration.",
    )
    is_daily_paid = fields.Boolean(
        string="Payé à la journée",
        default=False,
        help="Cochez si l'agent est rémunéré au forfait journalier (salaire fixe), "
             "pas à l'heure. Marqué « P » dans l'affichage du shift.",
    )
    active = fields.Boolean(default=True)

    _sql_constraints = [
        ('uniq_code', 'unique(code)',
         "Ce code de shift existe déjà — choisissez un code unique."),
    ]

    @api.depends('start_hour', 'end_hour')
    def _compute_crosses_midnight(self):
        for shift in self:
            shift.crosses_midnight = shift.end_hour <= shift.start_hour

    @api.depends('start_hour', 'end_hour', 'crosses_midnight')
    def _compute_duration(self):
        for shift in self:
            if shift.crosses_midnight:
                shift.duration = (24.0 - shift.start_hour) + shift.end_hour
            else:
                shift.duration = max(0.0, shift.end_hour - shift.start_hour)

    @api.depends('break_start_hour', 'break_end_hour')
    def _compute_break_duration(self):
        for shift in self:
            if shift.break_end_hour > shift.break_start_hour:
                shift.break_duration = shift.break_end_hour - shift.break_start_hour
            else:
                shift.break_duration = 0.0

    @api.depends('duration', 'break_duration')
    def _compute_paid_duration(self):
        for shift in self:
            shift.paid_duration = max(0.0, shift.duration - shift.break_duration)

    @api.depends('name', 'start_hour', 'end_hour', 'break_duration', 'is_daily_paid')
    def _compute_display_name(self):
        for shift in self:
            if not shift.name:
                shift.display_name = ''
                continue
            start_h = int(shift.start_hour)
            start_m = int(round((shift.start_hour - start_h) * 60))
            end_h = int(shift.end_hour)
            end_m = int(round((shift.end_hour - end_h) * 60))
            label = "%s %02dh%02d-%02dh%02d" % (
                shift.name, start_h, start_m, end_h, end_m,
            )
            if shift.break_duration > 0:
                br_h = int(shift.break_duration)
                br_m = int(round((shift.break_duration - br_h) * 60))
                label = "%s [%dh%02d pause]" % (label, br_h, br_m)
            if shift.is_daily_paid:
                label = "%s P" % label
            shift.display_name = label
