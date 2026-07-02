# -*- coding: utf-8 -*-
from odoo import models, fields, api


class GsPlanningAssignment(models.Model):
    """Affectation mensuelle : 1 employé sur 1 projet (site) pour 1 mois.

    Correspond à UNE ligne employé d'un bloc du fichier `planingdet.xls`.
    Porte les attributs constants sur le mois (fonction, rôle, jour de repos,
    mode de paie) et regroupe le détail jour par jour (`day_ids`).
    """
    _name = 'gs.planning.assignment'
    _description = "Affectation mensuelle (import planning détaillé)"
    _order = 'period_month desc, project_id, employee_id'
    _rec_name = 'display_name'

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
        help="Valeur brute de la colonne « EMPL » (FDM, ADN, CE…).",
    )
    period_month = fields.Date(string="Mois", required=True, index=True)
    service_type = fields.Selection(
        selection=[('proprete', "Propreté"), ('surveillance', "Surveillance")],
        string="Service", index=True,
    )
    rest_day = fields.Char(
        string="Jour de repos",
        help="Jour(s) de repos hebdomadaire, déduit de la colonne « Repot » "
             "quand elle contient un code jour (L, MA, MER, J, V, S, D, S/D…).",
    )
    schedule_note = fields.Char(
        string="Note horaire (Repot)",
        help="Contenu brut de « Repot » quand ce n'est pas un jour de repos "
             "(ex. « 1.68H 5/7 + 3.6 S », « F191 »).",
    )
    pay_type = fields.Selection(
        selection=[
            ('hour', "Horaire"),
            ('day', "Journalier"),
            ('month', "Forfait mensuel"),
        ],
        string="Mode de paie", index=True,
        help="Déduit des valeurs journalières : « 1 » = journalier, "
             "valeur > 24 = forfait mensuel, sinon horaire.",
    )
    day_ids = fields.One2many(
        'gs.planning.actual', 'assignment_id', string="Détail jour",
    )
    total_hours = fields.Float(
        string="Total heures", compute='_compute_totals', store=True,
    )
    worked_days = fields.Integer(
        string="Jours travaillés", compute='_compute_totals', store=True,
        help="Nombre de jours du mois avec au moins une activité.",
    )
    daily_paid_days = fields.Integer(
        string="Jours (forfait journalier)", compute='_compute_totals', store=True,
    )
    company_id = fields.Many2one(
        'res.company', string="Société",
        default=lambda self: self.env.company, required=True, index=True,
    )

    _sql_constraints = [
        ('uniq_emp_project_month',
         'unique(employee_id, project_id, period_month)',
         "Une seule affectation par employé, projet et mois."),
    ]

    @api.depends('day_ids.hours', 'day_ids.unit_type')
    def _compute_totals(self):
        for asg in self:
            asg.total_hours = sum(asg.day_ids.mapped('hours'))
            asg.worked_days = len(asg.day_ids.filtered(lambda d: d.quantity))
            asg.daily_paid_days = len(
                asg.day_ids.filtered(lambda d: d.unit_type == 'day'))

    @api.depends('employee_id', 'project_id', 'period_month')
    def _compute_display_name(self):
        for asg in self:
            parts = [
                asg.employee_id.name or '',
                asg.project_id.display_name or '',
                asg.period_month and asg.period_month.strftime('%m/%Y') or '',
            ]
            asg.display_name = " — ".join(p for p in parts if p)
