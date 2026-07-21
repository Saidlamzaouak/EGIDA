# -*- coding: utf-8 -*-
from odoo import models, fields, api


class HrEmployeeBase(models.AbstractModel):
    """Champs exposés aux PROFILS PUBLICS des employés.

    Défini sur l'abstrait `hr.employee.base` (partagé par hr.employee et
    hr.employee.public) : les chefs de projet / d'équipe, qui n'ont pas les
    droits RH, peuvent ainsi lire ces champs (nécessaires à l'affichage du
    planning) sans lever d'AccessError « champs non disponibles pour les
    profils publics »."""
    _inherit = 'hr.employee.base'

    cin = fields.Char(
        string="CIN",
        index=True,
        copy=False,
        tracking=True,
        help="Carte d'Identité Nationale / matricule (ex. G913831). "
             "Affiché sur le planning — lisible par tous les utilisateurs "
             "internes.",
    )


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    rest_weekday_ids = fields.Many2many(
        'gs.rest.weekday',
        'gs_employee_rest_weekday_rel', 'employee_id', 'weekday_id',
        string="Jours de repos hebdomadaires",
        help="Jour(s) de la semaine où l'employé est en repos par défaut "
             "(ex. Samedi + Dimanche). Repris automatiquement dans les "
             "affectations planning des projets (modifiable projet par "
             "projet). Aucun créneau n'est généré ces jours-là.",
    )
    cnss = fields.Char(
        string="N° CNSS",
        index=True,
        copy=False,
        tracking=True,
        groups="hr.group_hr_user",
        help="Numéro CNSS de l'employé (donnée sensible, réservée aux "
             "utilisateurs RH).",
    )

    @api.depends('name', 'cin')
    def _compute_display_name(self):
        """Inclut la CIN dans le display_name : 'Said Lamzaouak - G913831'."""
        super()._compute_display_name()
        for emp in self:
            if emp.cin and emp.display_name:
                emp.display_name = f"{emp.display_name} - {emp.cin}"
