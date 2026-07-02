# -*- coding: utf-8 -*-
from odoo import models, fields, api


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    cin = fields.Char(
        string="CIN",
        index=True,
        copy=False,
        tracking=True,
        help="Carte d'Identité Nationale (ex. G913831). Unique parmi les "
             "employés actifs.",
    )
    cnss = fields.Char(
        string="N° CNSS",
        index=True,
        copy=False,
        tracking=True,
        help="Numéro CNSS de l'employé. Unique parmi les employés actifs.",
    )

    @api.depends('name', 'cin')
    def _compute_display_name(self):
        """Inclut la CIN dans le display_name : 'Said Lamzaouak - G913831'."""
        super()._compute_display_name()
        for emp in self:
            if emp.cin and emp.display_name:
                emp.display_name = f"{emp.display_name} - {emp.cin}"
