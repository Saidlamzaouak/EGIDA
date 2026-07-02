# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError


class GsProjectPlanningLine(models.Model):
    _name = 'gs.project.planning.line'
    _description = 'Affectation employé / rôle / shift sur un projet'
    _rec_name = 'employee_id'

    project_id = fields.Many2one(
        'project.project', string='Projet',
        required=True, ondelete='cascade', index=True,
    )
    employee_id = fields.Many2one(
        'hr.employee', string='Employé',
        required=True, ondelete='restrict', index=True,
    )
    role_id = fields.Many2one(
        'planning.role', string='Rôle',
        required=True,
    )
    shift_id = fields.Many2one(
        'gs.planning.shift', string='Shift',
        required=True, ondelete='restrict',
        default=lambda self: self.env.ref(
            'gs_project_planning.shift_security_morning',
            raise_if_not_found=False,
        ),
    )
    start_hour = fields.Float(related='shift_id.start_hour', store=True)
    end_hour = fields.Float(related='shift_id.end_hour', store=True)
    crosses_midnight = fields.Boolean(related='shift_id.crosses_midnight', store=True)
    # Pause : héritée du shift par défaut mais éditable sur la ligne.
    # Permet d'avoir le même shift sur 2 projets avec des pauses différentes.
    break_start_hour = fields.Float(
        string="Début pause",
        default=0.0,
        help="Heure de début de pause (ex. 13.0). Initialisée depuis le "
             "shift mais modifiable avant génération du planning.",
    )
    break_end_hour = fields.Float(
        string="Fin pause",
        default=0.0,
        help="Heure de fin de pause (ex. 15.0).",
    )
    break_duration = fields.Float(
        string="Pause (h)",
        compute='_compute_break_duration', store=True,
    )
    company_id = fields.Many2one(
        related='project_id.company_id', store=True,
    )

    _sql_constraints = [
        ('uniq_employee_per_project',
         'unique(project_id, employee_id)',
         "Un même employé ne peut être ajouté qu'une seule fois sur un projet."),
    ]

    @api.depends('break_start_hour', 'break_end_hour')
    def _compute_break_duration(self):
        for line in self:
            if line.break_end_hour > line.break_start_hour:
                line.break_duration = line.break_end_hour - line.break_start_hour
            else:
                line.break_duration = 0.0

    @api.onchange('shift_id')
    def _onchange_shift_id_role(self):
        """Recopie le rôle ET la pause par défaut du shift sur la ligne."""
        for line in self:
            if not line.shift_id:
                continue
            if line.shift_id.role_id and not line.role_id:
                line.role_id = line.shift_id.role_id
            # Pause : on recopie systématiquement les défauts du shift
            # (l'utilisateur peut ensuite ajuster avant la génération).
            line.break_start_hour = line.shift_id.break_start_hour
            line.break_end_hour = line.shift_id.break_end_hour

    @api.onchange('employee_id')
    def _onchange_employee_default_role(self):
        for line in self:
            if line.employee_id and not line.role_id:
                role = line.employee_id.default_planning_role_id
                if role:
                    line.role_id = role

    @api.constrains('employee_id', 'project_id')
    def _check_employee_exclusivity(self):
        """Un employé ne peut être affecté qu'à UN seul projet dont la
        période [date_start, date] chevauche celle d'un autre projet.

        Skipped pendant l'install/upgrade pour éviter les faux conflits avec
        des données démos résiduelles ou des recalculs intermédiaires.

        Skipped aussi à l'import du planning détaillé (contexte
        skip_planning_exclusivity) : les agents multi-sites (femmes de ménage)
        figurent légitimement sur plusieurs projets du même mois."""
        if (self.env.context.get('install_mode')
                or self.env.context.get('module')
                or self.env.context.get('skip_planning_exclusivity')):
            return
        for line in self:
            project = line.project_id
            if not (project.date_start and project.date):
                continue
            conflict = self.search([
                ('id', '!=', line.id),
                ('employee_id', '=', line.employee_id.id),
                ('project_id', '!=', project.id),
                ('project_id.date_start', '<=', project.date),
                ('project_id.date', '>=', project.date_start),
            ], limit=1)
            if conflict:
                raise ValidationError(_(
                    "%(emp)s est déjà affecté(e) au projet « %(other)s » "
                    "sur une période qui chevauche celle de « %(current)s ».\n"
                    "Un employé ne peut pas travailler sur 2 projets en même temps.",
                    emp=line.employee_id.name,
                    other=conflict.project_id.display_name,
                    current=project.display_name,
                ))

    @api.constrains('employee_id', 'project_id')
    def _check_employee_in_team(self):
        """L'employé sélectionné doit appartenir à l'équipe du projet."""
        for line in self:
            team = line.project_id.allowed_employee_ids
            if team and line.employee_id and line.employee_id not in team:
                raise ValidationError(_(
                    "%(emp)s ne fait pas partie de l'équipe du projet "
                    "« %(proj)s ». Ajoutez-le d'abord dans l'onglet "
                    "« Équipe du projet ».",
                    emp=line.employee_id.name,
                    proj=line.project_id.display_name,
                ))
