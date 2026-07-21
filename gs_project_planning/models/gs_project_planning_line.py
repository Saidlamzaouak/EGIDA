# -*- coding: utf-8 -*-
from datetime import timedelta

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
    rest_weekday_ids = fields.Many2many(
        'gs.rest.weekday',
        'gs_planning_line_rest_weekday_rel', 'line_id', 'weekday_id',
        string="Jours de repos",
        help="Jour(s) de repos de cet employé sur ce projet (ex. Samedi + "
             "Dimanche). Initialisé depuis la fiche employé, modifiable ici. "
             "Aucun créneau n'est généré ces jours-là lors de la génération / "
             "actualisation du planning.",
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

    @api.onchange('employee_id')
    def _onchange_employee_rest_weekday(self):
        """Pré-remplit les jours de repos depuis la fiche employé (modifiable)."""
        for line in self:
            if line.employee_id and not line.rest_weekday_ids:
                line.rest_weekday_ids = line.employee_id.sudo().rest_weekday_ids

    def _gs_daily_busy_segments(self):
        """Segments d'occupation (jour_semaine, h_début, h_fin) de cet agent sur
        une semaine générique, pour cette ligne.

        - jour_semaine suit date.weekday() : 0 = Lundi … 6 = Dimanche.
        - Les jours de repos (rest_weekday_ids) ne produisent aucun segment.
        - Un shift de nuit (crosses_midnight) occupe [start_hour, 24h] le jour
          de départ ET [0h, end_hour] le lendemain.
        La pause n'est PAS retranchée : l'agent reste indisponible pour un autre
        site pendant sa pause."""
        self.ensure_one()
        rest = set(self.rest_weekday_ids.mapped('dayofweek'))
        work_days = [d for d in range(7) if d not in rest]
        start, end = self.start_hour, self.end_hour
        segments = []
        for d in work_days:
            if self.crosses_midnight:
                segments.append((d, start, 24.0))
                if end > 0:
                    segments.append(((d + 1) % 7, 0.0, end))
            elif end > start:
                segments.append((d, start, end))
        return segments

    def _gs_period_overlap_weekdays(self, other):
        """Jours de semaine (0..6) réellement communs aux DEUX périodes projet.

        Sert à ne comparer les horaires que sur les jours où les deux projets
        tournent effectivement en même temps (évite un faux conflit quand le
        chevauchement de dates ne tombe que sur un jour de repos)."""
        lo = max(self.project_id.date_start, other.project_id.date_start)
        hi = min(self.project_id.date, other.project_id.date)
        if lo > hi:
            return set()
        if (hi - lo).days >= 6:
            return set(range(7))
        days, day = set(), lo
        while day <= hi:
            days.add(day.weekday())
            day += timedelta(days=1)
        return days

    @staticmethod
    def _gs_segments_overlap(segs_a, segs_b):
        """Vrai si deux occupations se recoupent le même jour, plage horaire
        stricte (dos-à-dos, ex. 06h→14h puis 14h→22h, N'est PAS un conflit)."""
        for da, sa, ea in segs_a:
            for db, sb, eb in segs_b:
                if da == db and sa < eb and sb < ea:
                    return True
        return False

    @api.constrains('employee_id', 'project_id', 'shift_id', 'start_hour',
                    'end_hour', 'crosses_midnight', 'rest_weekday_ids')
    def _check_employee_exclusivity(self):
        """Un agent ne peut pas être physiquement sur 2 projets EN MÊME TEMPS.

        Le contrôle porte sur les HORAIRES RÉELS, pas sur la simple période du
        projet : un agent multi-sites (ex. femme de ménage) peut légitimement
        figurer sur 2 projets du même mois tant que ses créneaux ne se
        chevauchent pas — jours différents (jours de repos) ou plages horaires
        disjointes (matin ici / après-midi là). Le conflit n'est levé que si,
        sur un jour réellement commun aux deux périodes, les plages horaires se
        recoupent.

        Skipped pendant l'install/upgrade (faux conflits sur données démo ou
        recalculs intermédiaires) et à l'import du planning détaillé
        (skip_planning_exclusivity)."""
        if (self.env.context.get('install_mode')
                or self.env.context.get('module')
                or self.env.context.get('skip_planning_exclusivity')):
            return
        for line in self:
            project = line.project_id
            if not (project.date_start and project.date):
                continue
            # Pré-filtre : autres affectations du même agent, autre projet, dont
            # la période chevauche (condition nécessaire — sans jour commun,
            # aucun conflit horaire possible).
            candidates = self.search([
                ('id', '!=', line.id),
                ('employee_id', '=', line.employee_id.id),
                ('project_id', '!=', project.id),
                ('project_id.date_start', '<=', project.date),
                ('project_id.date', '>=', project.date_start),
            ])
            if not candidates:
                continue
            line_segs = line._gs_daily_busy_segments()
            if not line_segs:
                continue
            for other in candidates:
                common = line._gs_period_overlap_weekdays(other)
                if not common:
                    continue
                segs_a = [s for s in line_segs if s[0] in common]
                segs_b = [s for s in other._gs_daily_busy_segments()
                          if s[0] in common]
                if line._gs_segments_overlap(segs_a, segs_b):
                    raise ValidationError(_(
                        "%(emp)s est déjà affecté(e) au projet « %(other)s » "
                        "sur un créneau qui chevauche ses horaires sur "
                        "« %(current)s » (même jour, plages horaires qui se "
                        "recoupent).\n"
                        "Un employé ne peut pas être à deux endroits en même "
                        "temps. Décalez l'horaire (matin / après-midi) ou "
                        "ajustez les jours de repos.",
                        emp=line.employee_id.name,
                        other=other.project_id.display_name,
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
