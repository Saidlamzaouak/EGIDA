# -*- coding: utf-8 -*-
from datetime import datetime, time as datetime_time, timedelta

import pytz

from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError


class GsPlanningAddAgentWizard(models.TransientModel):
    _name = 'gs.planning.add.agent.wizard'
    _description = "Ajouter un agent (hors équipe) sur une journée"

    project_id = fields.Many2one(
        'project.project', string="Projet",
        required=True, readonly=True, ondelete='cascade',
    )
    day = fields.Date(
        string="Journée", readonly=True,
        help="Journée du planning sur laquelle l'agent est ajouté.",
    )
    employee_id = fields.Many2one(
        'hr.employee', string="Agent",
        required=True,
        help="Agent à ajouter sur cette journée. Il n'a pas besoin de faire "
             "partie de l'équipe du projet : seule la non-superposition "
             "horaire avec ses autres créneaux est vérifiée.",
    )
    in_team = fields.Boolean(
        compute='_compute_in_team', string="Déjà dans l'équipe",
    )
    role_id = fields.Many2one(
        'planning.role', string="Rôle",
    )
    start_hour = fields.Float(
        string="Heure de début", default=8.0, required=True,
        help="Heure de début (format HH:MM). La date reste celle de la colonne.",
    )
    end_hour = fields.Float(
        string="Heure de fin", default=17.0, required=True,
        help="Heure de fin (format HH:MM). Si elle est ≤ à l'heure de début, "
             "le créneau est considéré comme un shift de nuit se terminant le "
             "lendemain.",
    )
    break_duration = fields.Float(
        string="Pause (h)", default=0.0,
        help="Heures de pause incluses dans le créneau mais non payées.",
    )
    is_daily_paid = fields.Boolean(
        string="Payé à la journée", default=False,
    )

    @api.depends('employee_id', 'project_id')
    def _compute_in_team(self):
        for wiz in self:
            wiz.in_team = bool(
                wiz.employee_id
                and wiz.employee_id in wiz.project_id.allowed_employee_ids
            )

    @api.constrains('start_hour', 'end_hour')
    def _check_hours(self):
        for wiz in self:
            for val, label in ((wiz.start_hour, _("de début")),
                               (wiz.end_hour, _("de fin"))):
                if val < 0 or val >= 24:
                    raise ValidationError(_(
                        "L'heure %s doit être comprise entre 00:00 et 23:59.",
                        label,
                    ))
            if wiz.start_hour == wiz.end_hour:
                raise ValidationError(_(
                    "L'heure de fin doit être différente de l'heure de début."
                ))

    def _hours_to_datetimes(self):
        """Construit les datetimes UTC (naïfs) début/fin depuis la journée fixe
        et les heures saisies. Si l'heure de fin est ≤ à celle de début, la fin
        bascule au lendemain (shift de nuit)."""
        self.ensure_one()
        if not self.day:
            raise UserError(_("La journée n'est pas définie."))
        tz_name = self.env.user.tz or 'Africa/Casablanca'
        tz = pytz.timezone(tz_name)

        def _split(h):
            hh = int(h)
            mm = int(round((h - hh) * 60))
            if mm == 60:  # arrondi 59.9967 -> 60
                hh, mm = hh + 1, 0
            return hh, mm

        sh, sm = _split(self.start_hour)
        eh, em = _split(self.end_hour)
        start_local = tz.localize(datetime.combine(self.day, datetime_time(sh, sm)))
        end_day = self.day + timedelta(days=1) if self.end_hour <= self.start_hour else self.day
        end_local = tz.localize(datetime.combine(end_day, datetime_time(eh, em)))
        return (
            start_local.astimezone(pytz.UTC).replace(tzinfo=None),
            end_local.astimezone(pytz.UTC).replace(tzinfo=None),
        )

    def action_confirm(self):
        self.ensure_one()
        if not self.project_id:
            raise UserError(_("Aucun projet sélectionné."))
        if not self.employee_id:
            raise UserError(_("Sélectionnez l'agent à ajouter."))

        start_dt, end_dt = self._hours_to_datetimes()
        # sudo() : permet à tout utilisateur autorisé à ouvrir le wizard (chef
        # de projet / chef d'équipe) de créer le créneau, même sans droit de
        # création direct sur planning.slot. Les contraintes métier
        # (@api.constrains : superposition, plafond, équipe) restent appliquées.
        Slot = self.env['planning.slot'].sudo()
        # is_extra_agent=True : bypass de la contrainte d'appartenance à
        # l'équipe et exclusion du plafond horaire quotidien. La contrainte de
        # non-superposition inter-projets reste appliquée pour éviter les
        # doubles réservations.
        new_slot = Slot.create({
            'project_id': self.project_id.id,
            'employee_id': self.employee_id.id,
            'resource_id': self.employee_id.resource_id.id or False,
            'role_id': self.role_id.id or False,
            'start_datetime': start_dt,
            'end_datetime': end_dt,
            'break_duration': self.break_duration or 0.0,
            'is_daily_paid': self.is_daily_paid,
            'company_id': self.project_id.company_id.id or self.env.company.id,
            'state': 'draft',
            'is_extra_agent': True,
        })
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Agent ajouté'),
                'message': _(
                    "%(emp)s a été ajouté au planning du %(day)s. "
                    "Le créneau reste à valider.",
                    emp=self.employee_id.name or '',
                    day=(self.day and self.day.strftime('%d/%m/%Y')) or '',
                ),
                'type': 'success',
                'sticky': False,
                'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'},
            },
        }
