# -*- coding: utf-8 -*-
from odoo import models, fields, api


class GsPlanningActual(models.Model):
    """Heures réalisées importées depuis les plannings détaillés mensuels
    (fichiers `planingdet.xls EGIDA …`).

    Une ligne = l'activité d'UN employé sur UN projet (site client) pour UN
    jour donné. Volontairement découplé de `planning.slot` : les fichiers
    sources ne donnent qu'une valeur par jour (sans heure de début/fin), et les
    contraintes de `planning.slot` (équipe, chevauchement, datetimes)
    rejetteraient ces données. Un même employé peut avoir plusieurs lignes le
    même jour sur des projets différents (ex. femmes de ménage multi-sites).
    """
    _name = 'gs.planning.actual'
    _description = "Heures réalisées (import planning détaillé)"
    _order = 'date desc, project_id, employee_id'
    _rec_name = 'display_name'

    assignment_id = fields.Many2one(
        'gs.planning.assignment', string="Affectation mensuelle",
        ondelete='cascade', index=True,
    )
    project_id = fields.Many2one(
        'project.project', string="Projet / site",
        required=True, ondelete='cascade', index=True,
    )
    employee_id = fields.Many2one(
        'hr.employee', string="Employé",
        required=True, ondelete='cascade', index=True,
    )
    role_id = fields.Many2one('planning.role', string="Rôle")
    function_label = fields.Char(
        string="Fonction (source)",
        help="Valeur brute de la colonne « EMPL » du fichier source "
             "(ex. FDM, ADN, CE, CHEF EQUIPE).",
    )
    date = fields.Date(string="Jour", required=True, index=True)
    period_month = fields.Date(
        string="Mois", index=True,
        help="Premier jour du mois importé — sert au regroupement et au "
             "remplacement lors d'un ré-import.",
    )
    quantity = fields.Float(
        string="Valeur (source)",
        help="Valeur brute de la cellule jour du fichier : nombre d'heures, "
             "ou « 1 » pour un travail à la journée, ou un forfait mensuel.",
    )
    unit_type = fields.Selection(
        selection=[
            ('hour', "Heures"),
            ('day', "Journée"),
            ('month', "Forfait mensuel"),
        ],
        string="Type", default='hour', index=True,
        help="« 1 » → Journée (forfait journalier) ; valeur > 24 → Forfait "
             "mensuel (cumul posé sur un seul jour) ; sinon Heures.",
    )
    hours = fields.Float(
        string="Heures", compute='_compute_measures', store=True,
        help="Heures réelles : égale la valeur seulement en mode « Heures ».",
    )
    worked_days = fields.Float(
        string="Jour travaillé", compute='_compute_measures', store=True,
        help="1 si une activité existe ce jour (mesure de comptage des jours).",
    )
    note = fields.Char(
        string="Note (Repot)",
        help="Valeur brute de la colonne « Repot » (ex. « 3h+s », « f201 »).",
    )
    service_type = fields.Selection(
        selection=[
            ('proprete', "Propreté"),
            ('surveillance', "Surveillance"),
        ],
        string="Service", index=True,
    )
    company_id = fields.Many2one(
        'res.company', string="Société",
        default=lambda self: self.env.company,
        required=True, index=True,
    )

    _sql_constraints = [
        ('uniq_emp_project_day',
         'unique(employee_id, project_id, date)',
         "Une seule ligne d'heures réalisées par employé, projet et jour."),
    ]

    @api.depends('quantity', 'unit_type')
    def _compute_measures(self):
        for rec in self:
            rec.hours = rec.quantity if rec.unit_type == 'hour' else 0.0
            rec.worked_days = 1.0 if rec.quantity else 0.0

    @api.depends('employee_id', 'project_id', 'date')
    def _compute_display_name(self):
        for rec in self:
            parts = [
                rec.employee_id.name or '',
                rec.project_id.display_name or '',
                rec.date and rec.date.strftime('%d/%m/%Y') or '',
            ]
            rec.display_name = " — ".join(p for p in parts if p)
