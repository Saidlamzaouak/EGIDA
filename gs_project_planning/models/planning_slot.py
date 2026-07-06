# -*- coding: utf-8 -*-
from collections import defaultdict
from datetime import date, datetime, time as datetime_time, timedelta

import pytz

from odoo import models, fields, api, _
from odoo.exceptions import ValidationError, UserError, AccessError


class PlanningSlot(models.Model):
    _inherit = 'planning.slot'

    is_validated = fields.Boolean(
        string="Validé pour la paie", copy=False, default=False,
        help="Coché lorsque le manager a validé que ce shift a bien été "
             "effectué — une feuille de temps a été créée.",
    )
    is_absent = fields.Boolean(
        string="Absent", copy=False, default=False,
        help="Coché si l'employé était absent. Aucune feuille de temps "
             "n'est créée pour ce shift.",
    )
    timesheet_id = fields.Many2one(
        'account.analytic.line', string="Feuille de temps",
        copy=False, readonly=True, ondelete='set null',
    )
    work_entry_id = fields.Many2one(
        'hr.work.entry', string="Entrée de travail (paie)",
        copy=False, readonly=True, ondelete='set null',
        help="Lien vers l'entrée de travail consommée par le module de paie "
             "(hr.work.entry). Créée à la validation si l'agent a un contrat "
             "actif.",
    )
    is_past = fields.Boolean(
        compute='_compute_is_past', search='_search_is_past',
    )
    is_overdue = fields.Boolean(
        string="En retard (> 48h sans validation)",
        compute='_compute_is_overdue', search='_search_is_overdue',
        help="Shift terminé depuis plus de 48h et non encore validé ni marqué absent.",
    )
    duration_hours = fields.Float(
        string="Heures",
        compute='_compute_duration_hours', store=True,
        help="Durée payée du créneau (end - start - break_duration). "
             "Utilisée pour timesheets, work entries, agrégats, contraintes.",
    )
    break_duration = fields.Float(
        string="Pause (h)", default=0.0, copy=False,
        help="Heures de pause incluses dans le créneau mais non payées. "
             "Transmise depuis la planning_line à la génération.",
    )
    is_daily_paid = fields.Boolean(
        string="Payé à la journée", default=False, copy=False,
        help="Propagé depuis le shift à la génération. "
             "Affiche « /jour » au lieu des heures dans le kanban.",
    )
    is_night_shift = fields.Boolean(
        string="Shift de nuit", compute='_compute_is_night_shift', store=True,
        help="Vrai si le créneau passe minuit (start et end sur 2 jours).",
    )
    employee_cin = fields.Char(
        related='employee_id.cin', string="Matricule", store=False,
    )
    employee_short_name = fields.Char(
        related='employee_id.name', string="Nom court", store=False,
    )
    display_date_short = fields.Char(
        string="Date (court)", compute='_compute_display_horaire',
        help="Date au format DD/MM/YYYY sans nom du jour.",
    )
    break_label = fields.Char(
        string="Pause (libellé)", compute='_compute_break_label',
        help="Texte court de pause pour le kanban (ex. « 1h 00 »).",
    )
    break_range_label = fields.Char(
        string="Pause (intervalle)", compute='_compute_break_label',
        help="Texte de la plage de pause depuis la planning_line (ex. « 12h–13h »).",
    )
    display_date_range = fields.Char(
        string="Date affichée",
        compute='_compute_display_horaire',
    )
    display_time_range = fields.Char(
        string="Plage horaire",
        compute='_compute_display_horaire',
    )
    gs_status = fields.Selection(
        selection=[
            ('upcoming', '🔵 À venir'),
            ('pending', '🟠 À valider'),
            ('overdue', '🔴 En retard'),
            ('validated', '🟢 Validé'),
            ('absent', '⚫ Absent'),
        ],
        string="Statut",
        compute='_compute_gs_status', store=True,
        help="Statut métier consolidé pour les vues kanban / dashboard.",
    )
    replaces_slot_id = fields.Many2one(
        'planning.slot', string="Remplace le shift de",
        ondelete='set null', index=True, copy=False,
        help="Ce slot remplace un shift dont l'agent est absent. "
             "Permet de bypasser la contrainte d'appartenance à l'équipe du "
             "projet pour le remplaçant.",
    )
    replaced_by_slot_ids = fields.One2many(
        'planning.slot', 'replaces_slot_id',
        string="Remplacé par",
    )
    replacement_reason = fields.Char(
        string="Motif du remplacement", copy=False,
    )

    def _compute_is_past(self):
        now = fields.Datetime.now()
        for slot in self:
            slot.is_past = bool(slot.end_datetime and slot.end_datetime < now)

    def _search_is_past(self, operator, value):
        now = fields.Datetime.now()
        op = '<' if (operator == '=' and value) or (operator == '!=' and not value) else '>='
        return [('end_datetime', op, now)]

    def _compute_is_overdue(self):
        threshold = fields.Datetime.now() - timedelta(hours=48)
        for slot in self:
            slot.is_overdue = bool(
                slot.end_datetime
                and slot.end_datetime < threshold
                and not slot.is_validated
                and not slot.is_absent
            )

    def _search_is_overdue(self, operator, value):
        threshold = fields.Datetime.now() - timedelta(hours=48)
        want_overdue = (operator == '=' and value) or (operator == '!=' and not value)
        if want_overdue:
            return [
                ('end_datetime', '<', threshold),
                ('is_validated', '=', False),
                ('is_absent', '=', False),
            ]
        return ['|', '|', '|',
                ('end_datetime', '>=', threshold),
                ('end_datetime', '=', False),
                ('is_validated', '=', True),
                ('is_absent', '=', True)]

    _DAY_NAMES_FR = ['Lun', 'Mar', 'Mer', 'Jeu', 'Ven', 'Sam', 'Dim']

    @api.depends('start_datetime', 'end_datetime')
    def _compute_display_horaire(self):
        """Format compact pour les vues kanban : '22:00 → 06:00 (J+1)'."""
        for slot in self:
            if not (slot.start_datetime and slot.end_datetime):
                slot.display_date_range = ''
                slot.display_date_short = ''
                slot.display_time_range = ''
                continue
            start_local = fields.Datetime.context_timestamp(slot, slot.start_datetime)
            end_local = fields.Datetime.context_timestamp(slot, slot.end_datetime)
            day_label = self._DAY_NAMES_FR[start_local.weekday()]
            slot.display_date_range = f"{day_label} {start_local.strftime('%d/%m/%Y')}"
            slot.display_date_short = start_local.strftime('%d/%m/%Y')
            cross_marker = " (J+1)" if start_local.date() != end_local.date() else ""
            slot.display_time_range = (
                f"{start_local.strftime('%H:%M')} → "
                f"{end_local.strftime('%H:%M')}{cross_marker}"
            )

    @api.depends(
        'start_datetime', 'end_datetime', 'resource_id.calendar_id.flexible_hours',
        'company_id.resource_calendar_id', 'allocated_percentage', 'is_absent')
    def _compute_allocated_hours(self):
        """Un agent absent (même remplacé) n'a aucun temps alloué.

        On garde tout le calcul standard d'Odoo (intervalles de travail,
        pourcentage…) puis on force à 0 les slots absents. Le remplaçant porte
        le temps sur son propre slot. Au décochage de l'absence, super()
        recalcule le temps depuis les intervalles de travail : la valeur est
        restaurée automatiquement pour les slots avec ressource.
        """
        super()._compute_allocated_hours()
        for slot in self.filtered('is_absent'):
            slot.allocated_hours = 0.0

    @api.depends('start_datetime', 'end_datetime', 'break_duration', 'is_absent')
    def _compute_duration_hours(self):
        for slot in self:
            # Un agent absent (même remplacé) n'a effectué aucune heure : sa
            # durée payée est nulle. Le remplaçant porte les heures sur son
            # propre slot. Cohérent avec toutes les agrégations du module qui
            # filtrent déjà is_absent = False (pivot/graphe inclus).
            if slot.is_absent:
                slot.duration_hours = 0.0
            elif slot.start_datetime and slot.end_datetime:
                gross = (slot.end_datetime - slot.start_datetime).total_seconds() / 3600.0
                slot.duration_hours = max(0.0, gross - (slot.break_duration or 0.0))
            else:
                slot.duration_hours = 0.0

    @api.depends('start_datetime', 'end_datetime')
    def _compute_is_night_shift(self):
        for slot in self:
            if slot.start_datetime and slot.end_datetime:
                start_local = fields.Datetime.context_timestamp(slot, slot.start_datetime)
                end_local = fields.Datetime.context_timestamp(slot, slot.end_datetime)
                slot.is_night_shift = start_local.date() != end_local.date()
            else:
                slot.is_night_shift = False

    @api.depends('break_duration', 'employee_id', 'project_id')
    def _compute_break_label(self):
        """Construit le libellé court de la pause pour le kanban.

        - break_label : durée formatée (ex. '1h 00', '30 min')
        - break_range_label : intervalle horaire récupéré sur la planning_line
          du projet pour cet employé (ex. '12h–13h')
        """
        for slot in self:
            br = slot.break_duration or 0.0
            if br <= 0:
                slot.break_label = ''
                slot.break_range_label = ''
                continue
            br_h = int(br)
            br_m = int(round((br - br_h) * 60))
            if br_h == 0:
                slot.break_label = "%d min" % br_m
            else:
                slot.break_label = "%dh %02d" % (br_h, br_m)

            range_txt = ''
            if slot.project_id and slot.employee_id:
                line = self.env['gs.project.planning.line'].search([
                    ('project_id', '=', slot.project_id.id),
                    ('employee_id', '=', slot.employee_id.id),
                ], limit=1)
                if line and line.break_start_hour < line.break_end_hour:
                    sh = int(line.break_start_hour)
                    sm = int(round((line.break_start_hour - sh) * 60))
                    eh = int(line.break_end_hour)
                    em = int(round((line.break_end_hour - eh) * 60))
                    if sm == 0 and em == 0:
                        range_txt = "%dh–%dh" % (sh, eh)
                    else:
                        range_txt = "%dh%02d–%dh%02d" % (sh, sm, eh, em)
            slot.break_range_label = range_txt

    @api.depends('is_validated', 'is_absent', 'end_datetime')
    def _compute_gs_status(self):
        """Statut métier : à venir / à valider / en retard / validé / absent."""
        now = fields.Datetime.now()
        overdue_threshold = now - timedelta(hours=48)
        for slot in self:
            if slot.is_absent:
                slot.gs_status = 'absent'
            elif slot.is_validated:
                slot.gs_status = 'validated'
            elif slot.end_datetime and slot.end_datetime < overdue_threshold:
                slot.gs_status = 'overdue'
            elif slot.end_datetime and slot.end_datetime < now:
                slot.gs_status = 'pending'
            else:
                slot.gs_status = 'upcoming'

    @api.constrains('employee_id', 'project_id', 'start_datetime', 'end_datetime')
    def _check_project_employee_exclusivity(self):
        """Aucun chevauchement horaire entre 2 projets pour le même employé."""
        for slot in self:
            if not (slot.employee_id and slot.project_id
                    and slot.start_datetime and slot.end_datetime):
                continue
            conflict = self.search([
                ('id', '!=', slot.id),
                ('employee_id', '=', slot.employee_id.id),
                ('project_id', '!=', False),
                ('project_id', '!=', slot.project_id.id),
                ('start_datetime', '<', slot.end_datetime),
                ('end_datetime', '>', slot.start_datetime),
            ], limit=1)
            if conflict:
                raise ValidationError(_(
                    "Conflit : %(emp)s a déjà un créneau sur le projet "
                    "« %(other)s » entre %(s)s et %(e)s.",
                    emp=slot.employee_id.name,
                    other=conflict.project_id.display_name,
                    s=conflict.start_datetime,
                    e=conflict.end_datetime,
                ))

    @api.constrains('employee_id', 'project_id', 'replaces_slot_id')
    def _check_employee_in_project_team(self):
        """L'employé doit faire partie de l'équipe du projet — sauf en remplacement."""
        for slot in self:
            if not (slot.employee_id and slot.project_id):
                continue
            if slot.replaces_slot_id:
                # Remplacement ponctuel : bypass de la team du projet.
                continue
            team = slot.project_id.allowed_employee_ids
            if team and slot.employee_id not in team:
                raise ValidationError(_(
                    "%(emp)s ne fait pas partie de l'équipe du projet "
                    "« %(proj)s ». Ajoutez-le d'abord dans l'onglet "
                    "« Équipe du projet ».",
                    emp=slot.employee_id.name,
                    proj=slot.project_id.display_name,
                ))

    @api.constrains('start_datetime', 'end_datetime', 'project_id', 'is_absent')
    def _check_project_daily_hour_limit(self):
        """Plafond horaire quotidien par projet (avec quota d'heures supp.).

        Les slots marqués absents ne sont PAS comptés : l'agent n'est pas
        physiquement là, donc ils ne consomment pas le quota du jour.
        """
        impacted = defaultdict(set)  # project -> {day, day, ...}
        for slot in self:
            if not (slot.project_id and slot.start_datetime and slot.end_datetime):
                continue
            if slot.project_id.daily_hour_limit <= 0:
                continue
            impacted[slot.project_id].add(slot.start_datetime.date())

        for project, days in impacted.items():
            cap = project.daily_hour_limit + project.overtime_hour_limit
            day_start = min(days)
            day_end = max(days) + timedelta(days=1)
            day_slots = self.search([
                ('project_id', '=', project.id),
                ('is_absent', '=', False),
                ('start_datetime', '>=', datetime.combine(day_start, datetime.min.time())),
                ('start_datetime', '<', datetime.combine(day_end, datetime.min.time())),
            ])
            hours_by_day = defaultdict(float)
            for s in day_slots:
                if not (s.start_datetime and s.end_datetime):
                    continue
                day = s.start_datetime.date()
                if day not in days:
                    continue
                hours_by_day[day] += s.duration_hours
            for day, total in hours_by_day.items():
                if total > cap + 0.001:
                    n_agents = len(project.allowed_employee_ids)
                    suggested = n_agents * 8.0
                    auto_hint = ""
                    if not project.daily_hour_limit_auto and n_agents:
                        auto_hint = _(
                            "\n→ Astuce : cochez « Plafond auto (N agents × 8h) » "
                            "sur le projet pour que le plafond passe à %(s).1f h "
                            "automatiquement (%(n)d agents).",
                            s=suggested, n=n_agents,
                        )
                    raise ValidationError(_(
                        "Projet « %(proj)s » : %(total).1f h planifiées le "
                        "%(day)s dépassent le plafond (%(base).1f h "
                        "+ %(ot).1f h supplémentaires autorisées).%(hint)s",
                        proj=project.display_name,
                        total=total,
                        day=day.strftime('%d/%m/%Y'),
                        base=project.daily_hour_limit,
                        ot=project.overtime_hour_limit,
                        hint=auto_hint,
                    ))

    # =========================================================================
    #  Validation pour la paie → Timesheets
    # =========================================================================

    def action_validate_for_payroll(self):
        """Valide les slots et crée les feuilles de temps + work entries.

        À la validation :
        - is_validated = True  (drapeau métier)
        - state = 'published'  (sortie du brouillon — sécurité au cas où
          l'utilisateur a oublié de publier le planning avant le shift)
        - timesheet_id ← account.analytic.line (coûts projet, facturation)
        - work_entry_id ← hr.work.entry (lu par hr_payroll pour le bulletin)
        """
        created_ts = self.env['account.analytic.line']
        created_we = 0
        for slot in self:
            slot._ensure_validatable()
            if slot.is_validated:
                continue
            ts = slot._create_timesheet_entry()
            we = slot._create_work_entry()
            updates = {'is_validated': True, 'timesheet_id': ts.id}
            if we:
                updates['work_entry_id'] = we.id
                created_we += 1
            if slot.state == 'draft':
                updates['state'] = 'published'
            slot.write(updates)
            created_ts |= ts
        msg_lines = [_("%d feuille(s) de temps créée(s).", len(created_ts))]
        if created_we:
            msg_lines.append(_(
                "%d entrée(s) de travail générée(s) pour la paie.",
                created_we,
            ))
        elif len(created_ts):
            msg_lines.append(_(
                "Aucune entrée de paie : vérifiez que les agents ont un "
                "contrat actif et un type d'entrée de travail défini."
            ))
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Planning validé'),
                'message': "\n".join(msg_lines),
                'type': 'success',
                'sticky': False,
                'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'},
            },
        }

    def action_mark_absent(self):
        """Marque l'employé absent — pas de timesheet généré."""
        for slot in self:
            if slot.is_validated:
                raise UserError(_(
                    "Le shift de %s a déjà été validé pour la paie. "
                    "Annulez d'abord la validation.",
                    slot.employee_id.name or '',
                ))
        # is_absent = True déclenche déjà la remise à 0 des durées via les
        # computes, mais on les force explicitement ici pour garantir le 0
        # même si l'ordre de recalcul change (l'agent absent n'a effectué
        # aucune heure — c'est son remplaçant qui les porte).
        self.write({
            'is_absent': True,
            'allocated_hours': 0.0,
            'duration_hours': 0.0,
        })
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Absence enregistrée'),
                'message': _("%d shift(s) marqué(s) absent(s).", len(self)),
                'type': 'warning',
                'sticky': False,
                'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'},
            },
        }

    def action_validate_day_for_payroll(self):
        """Valide TOUS les shifts non validés et non absents du même projet,
        sur la même journée locale que ce slot. Utilisé depuis le menu d'une
        carte kanban ('⋮ → Valider toute la journée')."""
        self.ensure_one()
        if not (self.start_datetime and self.project_id):
            raise UserError(_("Ce shift n'a pas de date ou de projet."))

        tz_name = self.env.user.tz or 'Africa/Casablanca'
        tz = pytz.timezone(tz_name)
        start_local = pytz.UTC.localize(self.start_datetime).astimezone(tz)
        local_day = start_local.date()

        day_start_local = tz.localize(datetime.combine(local_day, datetime_time.min))
        day_end_local = day_start_local + timedelta(days=1)
        day_start_utc = day_start_local.astimezone(pytz.UTC).replace(tzinfo=None)
        day_end_utc = day_end_local.astimezone(pytz.UTC).replace(tzinfo=None)

        sibling_slots = self.search([
            ('project_id', '=', self.project_id.id),
            ('start_datetime', '>=', day_start_utc),
            ('start_datetime', '<', day_end_utc),
            ('is_validated', '=', False),
            ('is_absent', '=', False),
        ])
        if not sibling_slots:
            raise UserError(_(
                "Aucun shift en attente de validation pour le %s sur ce projet.",
                local_day.strftime('%d/%m/%Y'),
            ))
        return sibling_slots.action_validate_for_payroll()

    def action_validate_batch_for_payroll(self):
        """Valide les shifts non validés et non absents parmi le recordset."""
        pending = self.filtered(lambda s: not s.is_validated and not s.is_absent)
        if not pending:
            raise UserError(_(
                "Aucun shift en attente de validation dans cette sélection."
            ))
        return pending.action_validate_for_payroll()

    @api.model
    def action_validate_by_domain(self, group_domain):
        """Valide tous les shifts en attente correspondant au domaine fourni."""
        full_domain = list(group_domain or []) + [
            ('is_validated', '=', False),
            ('is_absent', '=', False),
        ]
        slots = self.search(full_domain)
        if not slots:
            raise UserError(_("Aucun shift en attente pour cette colonne."))
        return slots.action_validate_for_payroll()

    @api.model
    def action_validate_day_value_for_payroll(self, day_value, parent_domain=None):
        """Valide les shifts du jour `day_value`, dans le scope `parent_domain`.

        - day_value : str ISO 'YYYY-MM-DD' (ou datetime ISO), reçu de la
          valeur du groupe kanban (start_datetime:day en TZ utilisateur).
        - parent_domain : domaine du kanban (filtre project_id, etc.).

        On reconstruit les bornes UTC de la journée locale puis on filtre.
        Approche robuste : ne dépend pas de l'API instable des Group OWL."""
        if not day_value:
            raise UserError(_("Aucune date fournie pour la colonne."))
        if isinstance(day_value, str):
            try:
                day = date.fromisoformat(day_value[:10])
            except ValueError:
                raise UserError(_("Valeur de jour invalide : %s", day_value))
        else:
            raise UserError(_("Format de jour non reconnu : %s", type(day_value)))

        tz_name = self.env.user.tz or 'Africa/Casablanca'
        tz = pytz.timezone(tz_name)
        day_start_local = tz.localize(datetime.combine(day, datetime_time.min))
        day_end_local = day_start_local + timedelta(days=1)
        day_start_utc = day_start_local.astimezone(pytz.UTC).replace(tzinfo=None)
        day_end_utc = day_end_local.astimezone(pytz.UTC).replace(tzinfo=None)

        full_domain = list(parent_domain or []) + [
            ('start_datetime', '>=', day_start_utc),
            ('start_datetime', '<', day_end_utc),
            ('is_validated', '=', False),
            ('is_absent', '=', False),
        ]
        slots = self.search(full_domain)
        if not slots:
            raise UserError(_(
                "Aucun shift en attente pour le %s dans cette colonne.",
                day.strftime('%d/%m/%Y'),
            ))
        return slots.action_validate_for_payroll()

    def action_open_overtime_wizard(self):
        """Ouvre le wizard d'ajout d'heures supp pour ce shift."""
        self.ensure_one()
        if self.is_validated:
            raise UserError(_(
                "Ce shift est déjà validé pour la paie. Impossible "
                "d'ajouter des heures supp. Annulez la validation d'abord."
            ))
        if self.is_absent:
            raise UserError(_(
                "Ce shift est marqué absent. Aucune heure supp possible."
            ))
        if not self.project_id:
            raise UserError(_("Ce shift n'est pas lié à un projet."))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Heures supp — %s', self.employee_id.name or ''),
            'res_model': 'gs.planning.overtime.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_slot_id': self.id},
        }

    def action_open_replace_wizard(self):
        """Ouvre le wizard de remplacement pour ce slot absent."""
        self.ensure_one()
        if not self.is_absent:
            raise UserError(_(
                "Seuls les shifts marqués « absent » peuvent être remplacés. "
                "Marquez d'abord l'agent absent avant de lui chercher un remplaçant."
            ))
        if self.replaced_by_slot_ids:
            raise UserError(_(
                "Ce shift a déjà un remplaçant : %s.",
                ", ".join(self.replaced_by_slot_ids.mapped('employee_id.name')),
            ))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Remplacer le shift de %s', self.employee_id.name or ''),
            'res_model': 'gs.planning.replace.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_original_slot_id': self.id},
        }

    def action_cancel_validation(self):
        """Annule la validation : supprime le timesheet + la work entry."""
        cancelled = 0
        for slot in self:
            if slot.timesheet_id:
                slot.timesheet_id.unlink()
            if slot.work_entry_id:
                # Si l'entrée est déjà validée côté paie, on la laisse
                if slot.work_entry_id.state in ('draft', 'conflict'):
                    slot.work_entry_id.unlink()
            slot.write({
                'is_validated': False,
                'timesheet_id': False,
                'work_entry_id': False,
                'is_absent': False,
            })
            cancelled += 1
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Validation annulée'),
                'message': _("%d shift(s) réinitialisé(s).", cancelled),
                'type': 'info',
                'sticky': False,
                'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'},
            },
        }

    def _ensure_validatable(self):
        self.ensure_one()
        if self.is_absent:
            raise UserError(_(
                "Le shift de %s est marqué « absent ». "
                "Décochez d'abord pour le valider.",
                self.employee_id.name or '',
            ))
        if not self.employee_id:
            raise UserError(_("Le shift n'a pas d'employé assigné."))
        if not self.project_id:
            raise UserError(_("Le shift n'est pas lié à un projet."))
        if not self.start_datetime or not self.end_datetime:
            raise UserError(_("Le shift n'a pas de plage horaire."))
        if not self.project_id.allow_timesheets:
            raise UserError(_(
                "Le projet « %s » n'autorise pas les feuilles de temps. "
                "Activez « Timesheets » dans les paramètres du projet.",
                self.project_id.display_name,
            ))

    def _create_timesheet_entry(self):
        self.ensure_one()
        name = self.role_id.name or _("Shift planning")
        if self.employee_id:
            name = "%s — %s" % (name, self.employee_id.name)
        return self.env['account.analytic.line'].create({
            'name': name,
            'date': self.start_datetime.date(),
            'employee_id': self.employee_id.id,
            'project_id': self.project_id.id,
            'unit_amount': self.duration_hours,
            'company_id': self.company_id.id or self.env.company.id,
        })

    def _create_work_entry(self):
        """Crée une hr.work.entry consommée par le module de paie.

        Pré-requis :
        - hr_payroll installé (donc hr.work.entry disponible)
        - L'agent doit avoir un contrat actif
        - Un work_entry_type doit être déterminable (via le contrat ou un défaut)

        Si l'une de ces conditions manque, on retourne False sans erreur :
        la validation pour timesheet réussit, juste pas de work entry.
        """
        self.ensure_one()
        if 'hr.work.entry' not in self.env:
            return False
        contract = self.employee_id.contract_id
        if not contract:
            return False
        we_type = (
            contract.structure_type_id.default_work_entry_type_id
            if hasattr(contract, 'structure_type_id') else False
        )
        if not we_type:
            we_type = self.env.ref(
                'hr_work_entry.work_entry_type_attendance',
                raise_if_not_found=False,
            )
        if not we_type:
            return False
        # L'entrée de travail (paie) est optionnelle : si l'utilisateur qui
        # valide (ex. Chef de projet) n'a pas les droits RH/paie pour créer une
        # hr.work.entry, on n'échoue pas — la validation timesheet reste faite,
        # la paie sera générée par un profil RH.
        try:
            return self.env['hr.work.entry'].create({
                'name': "Shift %s — %s" % (
                    self.role_id.name or _("Planning"),
                    self.employee_id.name or '',
                ),
                'employee_id': self.employee_id.id,
                'contract_id': contract.id,
                'date_start': self.start_datetime,
                'date_stop': self.end_datetime,
                'work_entry_type_id': we_type.id,
                'state': 'draft',
            })
        except AccessError:
            return False
