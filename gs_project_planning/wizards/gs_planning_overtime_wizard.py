# -*- coding: utf-8 -*-
from datetime import datetime, time as datetime_time, timedelta

import pytz

from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError


class GsPlanningOvertimeWizard(models.TransientModel):
    _name = 'gs.planning.overtime.wizard'
    _description = "Ajouter des heures supplémentaires à un shift"

    slot_id = fields.Many2one(
        'planning.slot', string="Shift",
        required=True, readonly=True, ondelete='cascade',
    )
    project_id = fields.Many2one(
        related='slot_id.project_id', string="Projet", readonly=True,
    )
    employee_id = fields.Many2one(
        related='slot_id.employee_id', string="Agent", readonly=True,
    )
    start_datetime = fields.Datetime(
        related='slot_id.start_datetime', string="Début actuel", readonly=True,
    )
    end_datetime = fields.Datetime(
        related='slot_id.end_datetime', string="Fin actuelle", readonly=True,
    )
    current_hours = fields.Float(
        compute='_compute_quota', string="Durée actuelle du shift", readonly=True,
    )
    daily_total_hours = fields.Float(
        compute='_compute_quota', string="Total déjà planifié ce jour", readonly=True,
    )
    overtime_limit = fields.Float(
        compute='_compute_quota',
        string="Heures supp. autorisées / jour", readonly=True,
        help="Champ « Heures supp. autorisées / jour » du projet.",
    )
    overtime_used = fields.Float(
        compute='_compute_quota',
        string="Heures supp. déjà utilisées ce jour", readonly=True,
    )
    available_hours = fields.Float(
        compute='_compute_quota',
        string="Heures supp. disponibles", readonly=True,
        help="Maximum d'heures supp encore ajoutables = overtime_hour_limit "
             "− OT déjà consommées aujourd'hui.",
    )
    hours_to_add = fields.Float(
        string="Heures à ajouter", default=0.0, required=True,
        help="Modifiable uniquement via les boutons + 1h / − 1h.",
    )

    @api.depends('slot_id')
    def _compute_quota(self):
        for wiz in self:
            slot = wiz.slot_id
            wiz.current_hours = 0.0
            wiz.daily_total_hours = 0.0
            wiz.overtime_limit = 0.0
            wiz.overtime_used = 0.0
            wiz.available_hours = 0.0
            if not (slot and slot.start_datetime and slot.end_datetime and slot.project_id):
                continue
            wiz.current_hours = (
                (slot.end_datetime - slot.start_datetime).total_seconds() / 3600.0
            )
            project = slot.project_id
            wiz.overtime_limit = project.overtime_hour_limit or 0.0

            tz_name = self.env.user.tz or 'Africa/Casablanca'
            tz = pytz.timezone(tz_name)
            start_local = pytz.UTC.localize(slot.start_datetime).astimezone(tz)
            local_day = start_local.date()
            day_start_local = tz.localize(datetime.combine(local_day, datetime_time.min))
            day_end_local = day_start_local + timedelta(days=1)
            day_start_utc = day_start_local.astimezone(pytz.UTC).replace(tzinfo=None)
            day_end_utc = day_end_local.astimezone(pytz.UTC).replace(tzinfo=None)

            day_slots = self.env['planning.slot'].search([
                ('project_id', '=', project.id),
                ('is_absent', '=', False),
                ('start_datetime', '>=', day_start_utc),
                ('start_datetime', '<', day_end_utc),
            ])
            total = 0.0
            for s in day_slots:
                if s.start_datetime and s.end_datetime:
                    total += (s.end_datetime - s.start_datetime).total_seconds() / 3600.0
            wiz.daily_total_hours = total
            # OT déjà consommée = ce qui dépasse la limite quotidienne normale
            wiz.overtime_used = max(0.0, total - (project.daily_hour_limit or 0.0))
            wiz.available_hours = max(0.0, wiz.overtime_limit - wiz.overtime_used)

    @api.constrains('hours_to_add')
    def _check_hours_to_add(self):
        for wiz in self:
            if wiz.hours_to_add < 0:
                raise ValidationError(_("Le nombre d'heures ne peut pas être négatif."))
            if wiz.hours_to_add > wiz.available_hours + 0.001:
                raise ValidationError(_(
                    "Vous demandez %(asked).1f h mais seules %(avail).1f h "
                    "d'heures supp restent disponibles aujourd'hui sur "
                    "le projet « %(proj)s »\n"
                    "(quota OT : %(limit).1f h ; déjà utilisées : %(used).1f h).",
                    asked=wiz.hours_to_add,
                    avail=wiz.available_hours,
                    proj=wiz.project_id.display_name,
                    limit=wiz.overtime_limit,
                    used=wiz.overtime_used,
                ))

    def action_increment_hour(self):
        """Bouton +1h : augmente hours_to_add de 1 si la marge le permet."""
        self.ensure_one()
        candidate = self.hours_to_add + 1.0
        if candidate > self.available_hours + 0.001:
            raise UserError(_(
                "Impossible : %(c).1f h dépasserait le quota OT restant "
                "(%(a).1f h disponibles).",
                c=candidate, a=self.available_hours,
            ))
        self.hours_to_add = candidate
        return self._reopen_self()

    def action_decrement_hour(self):
        """Bouton −1h : diminue hours_to_add de 1, plancher à 0."""
        self.ensure_one()
        self.hours_to_add = max(0.0, self.hours_to_add - 1.0)
        return self._reopen_self()

    def _reopen_self(self):
        """Rouvre le wizard sur la même instance pour rafraîchir l'affichage
        après un click sur +1h / −1h."""
        return {
            'type': 'ir.actions.act_window',
            'name': _('Heures supp'),
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def action_confirm(self):
        self.ensure_one()
        if self.hours_to_add <= 0:
            raise UserError(_(
                "Utilisez les boutons + 1h / − 1h pour choisir le nombre "
                "d'heures à ajouter."
            ))
        slot = self.slot_id
        if not slot.exists():
            raise UserError(_("Le shift n'existe plus."))
        if slot.is_validated:
            raise UserError(_(
                "Ce shift est déjà validé pour la paie. Annulez la validation "
                "avant d'ajouter des heures."
            ))
        if slot.is_absent:
            raise UserError(_(
                "Ce shift est marqué absent. Aucune heure supplémentaire "
                "ne peut être ajoutée."
            ))

        new_end = slot.end_datetime + timedelta(hours=self.hours_to_add)
        # sudo() : cohérent avec l'ouverture de l'action aux chefs ; les
        # contraintes métier (@api.constrains) restent appliquées.
        slot.sudo().write({'end_datetime': new_end})

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Heures supp. ajoutées'),
                'message': _(
                    "%(h).1f h ajoutées au shift de %(emp)s. Nouvelle fin : %(end)s.",
                    h=self.hours_to_add,
                    emp=self.employee_id.name or '',
                    end=fields.Datetime.context_timestamp(self, new_end).strftime('%d/%m/%Y %H:%M'),
                ),
                'type': 'success',
                'sticky': False,
                'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'},
            },
        }
