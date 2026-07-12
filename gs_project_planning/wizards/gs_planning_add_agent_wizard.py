# -*- coding: utf-8 -*-
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
    start_datetime = fields.Datetime(
        string="Début", required=True,
    )
    end_datetime = fields.Datetime(
        string="Fin", required=True,
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

    @api.constrains('start_datetime', 'end_datetime')
    def _check_dates(self):
        for wiz in self:
            if (wiz.start_datetime and wiz.end_datetime
                    and wiz.end_datetime <= wiz.start_datetime):
                raise ValidationError(_(
                    "L'heure de fin doit être postérieure à l'heure de début."
                ))

    def action_confirm(self):
        self.ensure_one()
        if not self.project_id:
            raise UserError(_("Aucun projet sélectionné."))
        if not self.employee_id:
            raise UserError(_("Sélectionnez l'agent à ajouter."))

        Slot = self.env['planning.slot']
        # is_extra_agent=True : bypass de la contrainte d'appartenance à
        # l'équipe et exclusion du plafond horaire quotidien. La contrainte de
        # non-superposition inter-projets reste appliquée pour éviter les
        # doubles réservations.
        new_slot = Slot.create({
            'project_id': self.project_id.id,
            'employee_id': self.employee_id.id,
            'resource_id': self.employee_id.resource_id.id or False,
            'role_id': self.role_id.id or False,
            'start_datetime': self.start_datetime,
            'end_datetime': self.end_datetime,
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
