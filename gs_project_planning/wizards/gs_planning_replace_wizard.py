# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError


class GsPlanningReplaceWizard(models.TransientModel):
    _name = 'gs.planning.replace.wizard'
    _description = "Remplacer un shift absent par un autre agent"

    original_slot_id = fields.Many2one(
        'planning.slot', string="Shift d'origine",
        required=True, readonly=True, ondelete='cascade',
    )
    original_employee_id = fields.Many2one(
        related='original_slot_id.employee_id', string="Agent absent", readonly=True,
    )
    project_id = fields.Many2one(
        related='original_slot_id.project_id', string="Projet", readonly=True,
    )
    start_datetime = fields.Datetime(
        related='original_slot_id.start_datetime', string="Début", readonly=True,
    )
    end_datetime = fields.Datetime(
        related='original_slot_id.end_datetime', string="Fin", readonly=True,
    )
    role_id = fields.Many2one(
        related='original_slot_id.role_id', string="Rôle", readonly=True,
    )
    replacement_employee_id = fields.Many2one(
        'hr.employee', string="Remplaçant",
        required=True,
        help="L'agent qui remplace. Il peut appartenir à un autre projet : "
             "seule la non-superposition horaire est vérifiée.",
    )
    reason = fields.Char(string="Motif", help="Maladie, congé, etc.")

    @api.constrains('replacement_employee_id', 'original_slot_id')
    def _check_replacement_not_same(self):
        for wiz in self:
            if (wiz.replacement_employee_id
                    and wiz.original_employee_id
                    and wiz.replacement_employee_id == wiz.original_employee_id):
                raise ValidationError(_(
                    "Le remplaçant ne peut pas être l'agent absent lui-même."
                ))

    def action_confirm(self):
        self.ensure_one()
        if not self.original_slot_id.is_absent:
            raise UserError(_(
                "Le shift d'origine n'est plus marqué « absent ». "
                "Annulez et recommencez le processus."
            ))
        if self.original_slot_id.replaced_by_slot_ids:
            raise UserError(_(
                "Ce shift a déjà un remplaçant : %s.",
                ", ".join(
                    self.original_slot_id.replaced_by_slot_ids.mapped('employee_id.name')
                ),
            ))

        # sudo() : permet aux chefs (sans droit de création direct sur
        # planning.slot) de créer le créneau de remplacement. Les contraintes
        # métier (@api.constrains) restent appliquées.
        Slot = self.env['planning.slot'].sudo()
        new_slot = Slot.create({
            'project_id': self.project_id.id,
            'employee_id': self.replacement_employee_id.id,
            'resource_id': self.replacement_employee_id.resource_id.id or False,
            'role_id': self.role_id.id or False,
            'start_datetime': self.start_datetime,
            'end_datetime': self.end_datetime,
            'company_id': self.original_slot_id.company_id.id or self.env.company.id,
            'state': 'draft',
            'replaces_slot_id': self.original_slot_id.id,
            'replacement_reason': self.reason or False,
        })
        return {
            'type': 'ir.actions.act_window',
            'name': _('Slot de remplacement'),
            'res_model': 'planning.slot',
            'res_id': new_slot.id,
            'view_mode': 'form',
            'target': 'current',
        }
