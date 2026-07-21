# -*- coding: utf-8 -*-
from collections import defaultdict
from datetime import datetime, time, timedelta

import pytz

from odoo import models, fields, api, _
from odoo.exceptions import UserError


class ProjectProject(models.Model):
    _inherit = 'project.project'

    allowed_employee_ids = fields.Many2many(
        'hr.employee',
        'gs_project_allowed_employee_rel',
        'project_id', 'employee_id',
        string="Équipe du projet",
        help="Liste des employés autorisés à intervenir sur ce projet. "
             "La saisie d'affectations et de créneaux est restreinte à cette équipe.",
    )
    planning_line_ids = fields.One2many(
        'gs.project.planning.line', 'project_id',
        string='Affectations planning',
        copy=True,
    )
    planning_generated = fields.Boolean(
        string='Planning généré', copy=False, default=False,
        help="Coché lorsque les créneaux ont été générés. "
             "Empêche la double génération.",
    )
    planning_slot_count = fields.Integer(
        compute='_compute_planning_slot_count',
        string='Créneaux générés',
    )
    validated_slot_count = fields.Integer(
        compute='_compute_planning_slot_count',
        string='Créneaux validés',
    )
    pending_slot_count = fields.Integer(
        compute='_compute_planning_slot_count',
        string='Créneaux à valider',
    )
    draft_slot_count = fields.Integer(
        compute='_compute_planning_slot_count',
        string='Créneaux à publier',
    )
    daily_hour_limit = fields.Float(
        string="Limite horaire / jour (h)",
        default=0.0,
        help="Plafond d'heures planifiables par jour sur ce projet. "
             "Toute saisie qui ferait dépasser ce seuil est refusée. "
             "0 = pas de limite.",
    )
    daily_hour_limit_auto = fields.Boolean(
        string="Plafond auto (somme des durées de shifts)",
        default=True,
        help="Quand coché, le plafond se recalcule automatiquement à chaque "
             "modification de l'équipe ou des affectations planning :\n"
             "  • Si des lignes planning sont configurées → somme des durées "
             "    de chaque shift (ex. 2 lignes 12h = 24h, 3 lignes 8h + 1 ligne "
             "    4h = 28h).\n"
             "  • Sinon → fallback sur N agents × 8h.\n"
             "Décochez pour saisir une valeur manuelle qui ne sera plus écrasée.",
    )
    overtime_hour_limit = fields.Float(
        string="Heures supp. autorisées / jour (h)",
        default=0.0,
        help="Heures supplémentaires que vous autorisez à dépasser la limite "
             "quotidienne. Plafond réel = daily_hour_limit + overtime_hour_limit. "
             "0 = pas d'heures supp.",
    )
    overtime_hours_used = fields.Float(
        string="Heures supp. consommées (total projet)",
        compute='_compute_overtime_hours_used',
        help="Somme des heures effectivement placées au-dessus du quota normal "
             "sur toute la durée du projet.",
    )

    def _compute_auto_daily_hour_limit(self):
        """Calcule le plafond auto basé sur la somme des durées des planning
        lines configurées.

        Ex. : 3 agents Sécurité (8h chacun) + 1 Ménage (4h) → 28h
              2 lignes Sécurité 12h → 24h

        Si aucune ligne configurée, fallback sur N agents × 8h."""
        self.ensure_one()
        if self.planning_line_ids:
            total = 0.0
            for line in self.planning_line_ids:
                if line.crosses_midnight:
                    total += (24.0 - line.start_hour) + line.end_hour
                else:
                    total += max(0.0, line.end_hour - line.start_hour)
            return total
        return len(self.allowed_employee_ids) * 8.0

    @api.onchange('allowed_employee_ids', 'planning_line_ids', 'daily_hour_limit_auto')
    def _onchange_recalc_daily_hour_limit(self):
        for project in self:
            if project.daily_hour_limit_auto:
                project.daily_hour_limit = project._compute_auto_daily_hour_limit()

    @api.depends('daily_hour_limit')
    def _compute_overtime_hours_used(self):
        """Total des heures qui dépassent daily_hour_limit, sur tous les jours."""
        Slot = self.env['planning.slot']
        for project in self:
            if not project.id or project.daily_hour_limit <= 0:
                project.overtime_hours_used = 0.0
                continue
            slots = Slot.search([
                ('project_id', '=', project.id),
                ('is_absent', '=', False),
            ])
            hours_by_day = defaultdict(float)
            for s in slots:
                if not (s.start_datetime and s.end_datetime):
                    continue
                day = s.start_datetime.date()
                hours_by_day[day] += (s.end_datetime - s.start_datetime).total_seconds() / 3600.0
            project.overtime_hours_used = sum(
                max(0.0, total - project.daily_hour_limit)
                for total in hours_by_day.values()
            )

    def _compute_planning_slot_count(self):
        now = fields.Datetime.now()
        Slot = self.env['planning.slot']
        total = dict(Slot._read_group(
            domain=[('project_id', 'in', self.ids)],
            groupby=['project_id'], aggregates=['__count'],
        ))
        validated = dict(Slot._read_group(
            domain=[('project_id', 'in', self.ids), ('is_validated', '=', True)],
            groupby=['project_id'], aggregates=['__count'],
        ))
        pending = dict(Slot._read_group(
            domain=[
                ('project_id', 'in', self.ids),
                ('is_validated', '=', False),
                ('is_absent', '=', False),
                ('end_datetime', '<', now),
            ],
            groupby=['project_id'], aggregates=['__count'],
        ))
        draft = dict(Slot._read_group(
            domain=[('project_id', 'in', self.ids), ('state', '=', 'draft')],
            groupby=['project_id'], aggregates=['__count'],
        ))
        for project in self:
            project.planning_slot_count = total.get(project, 0)
            project.validated_slot_count = validated.get(project, 0)
            project.pending_slot_count = pending.get(project, 0)
            project.draft_slot_count = draft.get(project, 0)

    def write(self, vals):
        res = super().write(vals)
        # Recalcul auto du plafond quand l'équipe ou les lignes changent
        if 'allowed_employee_ids' in vals or 'planning_line_ids' in vals:
            for project in self:
                if project.daily_hour_limit_auto:
                    new_limit = project._compute_auto_daily_hour_limit()
                    if abs(new_limit - project.daily_hour_limit) > 0.001:
                        super(ProjectProject, project).write({
                            'daily_hour_limit': new_limit,
                        })
        if 'stage_id' in vals:
            won_stage = self.env.ref(
                'gs_project_planning.project_project_stage_won',
                raise_if_not_found=False,
            )
            if won_stage:
                for project in self:
                    if (project.stage_id == won_stage
                            and not project.planning_generated
                            and project.planning_line_ids):
                        project.action_generate_planning_slots()
        return res

    def action_open_project_config(self):
        """Ouvre la fiche complète du projet (configuration)."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Configuration — %s', self.name),
            'res_model': 'project.project',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_publish_planning(self):
        """Publie tous les slots du projet encore en brouillon.

        Les agents les verront alors dans leur appli Planning.
        """
        self.ensure_one()
        draft_slots = self.env['planning.slot'].search([
            ('project_id', '=', self.id),
            ('state', '=', 'draft'),
        ])
        if not draft_slots:
            raise UserError(_("Aucun créneau en brouillon à publier."))
        draft_slots.write({'state': 'published'})
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Planning publié'),
                'message': _(
                    "%d créneau(x) publié(s). Les agents les voient désormais "
                    "dans leur appli Planning.",
                    len(draft_slots),
                ),
                'type': 'success',
                'sticky': False,
            },
        }

    def action_unpublish_planning(self):
        """Repasse les slots publiés non validés en brouillon."""
        self.ensure_one()
        published_slots = self.env['planning.slot'].search([
            ('project_id', '=', self.id),
            ('state', '=', 'published'),
            ('is_validated', '=', False),
        ])
        if not published_slots:
            raise UserError(_("Aucun créneau publié à dépublier (les validés ne sont pas concernés)."))
        published_slots.write({'state': 'draft'})
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Planning dépublié'),
                'message': _("%d créneau(x) repassé(s) en brouillon.", len(published_slots)),
                'type': 'info',
                'sticky': False,
            },
        }

    def action_reset_planning(self):
        self.ensure_one()
        self.env['planning.slot'].search([
            ('project_id', '=', self.id),
            ('state', '=', 'draft'),
        ]).unlink()
        self.planning_generated = False
        return True

    def action_sync_shift_config(self):
        """Resynchronise les lignes d'affectation avec la config actuelle des shifts.

        Utile pour les projets déjà créés : si l'admin a modifié la pause
        (break_start_hour / break_end_hour) ou le rôle par défaut d'un shift
        APRÈS la création des lignes, ce bouton recopie les valeurs à jour
        depuis gs.planning.shift vers chaque planning_line.

        Ne touche pas aux slots déjà générés — utilisez « Actualiser le
        planning » ensuite pour propager aux créneaux futurs.
        """
        self.ensure_one()
        if not self.planning_line_ids:
            raise UserError(_(
                "Aucune ligne d'affectation à synchroniser sur ce projet."
            ))
        updated = 0
        for line in self.planning_line_ids:
            shift = line.shift_id
            if not shift:
                continue
            vals = {}
            if line.break_start_hour != shift.break_start_hour:
                vals['break_start_hour'] = shift.break_start_hour
            if line.break_end_hour != shift.break_end_hour:
                vals['break_end_hour'] = shift.break_end_hour
            if shift.role_id and not line.role_id:
                vals['role_id'] = shift.role_id.id
            if vals:
                line.write(vals)
                updated += 1
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Configuration shifts synchronisée'),
                'message': _(
                    "%(n)d ligne(s) mise(s) à jour sur %(t)d. "
                    "Cliquez sur « Actualiser le planning » pour propager "
                    "aux créneaux futurs.",
                    n=updated, t=len(self.planning_line_ids),
                ),
                'type': 'success' if updated else 'info',
                'sticky': False,
                'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'},
            },
        }

    def action_apply_employee_rest_weekday(self):
        """Recopie les « Jours de repos » de la fiche employé vers chaque ligne
        Planning Resources du/des projet(s) sélectionné(s).

        Ne pousse que les valeurs réellement définies sur l'employé : si un
        employé n'a aucun jour de repos, les éventuels jours saisis
        manuellement sur la ligne (override projet) sont conservés.
        """
        updated = 0
        projects_touched = 0
        for project in self:
            project_updated = 0
            for line in project.planning_line_ids:
                emp_rest = line.employee_id.sudo().rest_weekday_ids
                if emp_rest and line.rest_weekday_ids != emp_rest:
                    line.rest_weekday_ids = [(6, 0, emp_rest.ids)]
                    project_updated += 1
            if project_updated:
                projects_touched += 1
                updated += project_updated

        if not updated:
            raise UserError(_(
                "Aucune ligne mise à jour : les jours de repos des lignes "
                "correspondent déjà à ceux des fiches employés, ou aucun jour "
                "de repos n'est renseigné sur les employés concernés."
            ))
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Jours de repos synchronisés'),
                'message': _(
                    "%(n)d ligne(s) mise(s) à jour sur %(p)d projet(s) depuis "
                    "les fiches employés. Cliquez sur « Actualiser le "
                    "planning » pour propager aux créneaux futurs.",
                    n=updated, p=projects_touched,
                ),
                'type': 'success',
                'sticky': False,
                'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'},
            },
        }

    def action_apply_line_hours_to_slots(self):
        """Applique la config horaire actuelle des lignes « Planning Resources »
        aux créneaux FUTURS déjà générés.

        Pour chaque projet sélectionné, et pour chaque ligne (= 1 employé),
        met à jour l'heure de début/fin (depuis le shift de la ligne) et la
        pause (depuis la ligne) des créneaux futurs, en conservant leur DATE.

        - Ne touche qu'aux créneaux à partir de demain (fuseau utilisateur),
          non validés et non absents. Le passé et les créneaux validés pour la
          paie ne sont jamais modifiés.
        - Déclenchée en lot depuis Action → « Appliquer les horaires du
          planning » (liste ou formulaire projet).
        """
        Slot = self.env['planning.slot']
        updated = 0
        touched_projects = 0

        for project in self:
            if not project.planning_line_ids:
                continue
            tz_name = (self.env.user.tz
                       or project.company_id.resource_calendar_id.tz
                       or 'Africa/Casablanca')
            local_tz = pytz.timezone(tz_name)

            today_local = datetime.now(local_tz).date()
            tomorrow_local = today_local + timedelta(days=1)
            cutoff_utc = local_tz.localize(
                datetime.combine(tomorrow_local, time.min)
            ).astimezone(pytz.UTC).replace(tzinfo=None)

            project_updated = 0
            for line in project.planning_line_ids:
                if not line.employee_id:
                    continue
                slots = Slot.search([
                    ('project_id', '=', project.id),
                    ('employee_id', '=', line.employee_id.id),
                    ('start_datetime', '>=', cutoff_utc),
                    ('is_validated', '=', False),
                    ('is_absent', '=', False),
                ])
                for slot in slots:
                    if not slot.start_datetime:
                        continue
                    # Jour local du créneau (on conserve la date, on change
                    # seulement les heures).
                    local_start = pytz.UTC.localize(
                        slot.start_datetime).astimezone(local_tz)
                    day = local_start.date()
                    vals = self._slot_time_vals(line, day, local_tz)
                    slot.write(vals)
                    project_updated += 1

            if project_updated:
                touched_projects += 1
                updated += project_updated

        if not updated:
            raise UserError(_(
                "Aucun créneau futur non validé à mettre à jour sur la "
                "sélection. Vérifiez que les projets ont des lignes "
                "« Planning Resources » et des créneaux futurs générés."
            ))

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Horaires appliqués'),
                'message': _(
                    "%(n)d créneau(x) mis à jour sur %(p)d projet(s). "
                    "Seuls les créneaux futurs non validés ont été modifiés.",
                    n=updated, p=touched_projects,
                ),
                'type': 'success',
                'sticky': False,
                'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'},
            },
        }

    def _slot_time_vals(self, line, day, local_tz):
        """Valeurs horaires d'un créneau pour une ligne un jour donné.

        Ne renvoie que les champs d'HORAIRE (début/fin/pause) — contrairement
        à _prepare_slot_vals, ne réinitialise ni l'état ni le rôle du créneau
        existant."""
        start_h = int(line.start_hour)
        start_m = int(round((line.start_hour - start_h) * 60))
        end_h = int(line.end_hour)
        end_m = int(round((line.end_hour - end_h) * 60))

        local_start = local_tz.localize(datetime.combine(day, time(start_h, start_m)))
        end_day = day + timedelta(days=1) if line.crosses_midnight else day
        local_end = local_tz.localize(datetime.combine(end_day, time(end_h, end_m)))

        return {
            'start_datetime': local_start.astimezone(pytz.UTC).replace(tzinfo=None),
            'end_datetime': local_end.astimezone(pytz.UTC).replace(tzinfo=None),
            'break_duration': line.break_duration or 0.0,
            'is_daily_paid': line.shift_id.is_daily_paid,
        }

    def action_open_planning_slots(self):
        self.ensure_one()
        kanban_view = self.env.ref('gs_project_planning.view_planning_slot_kanban_gs')
        list_view = self.env.ref('gs_project_planning.view_planning_slot_list_gs')
        graph_view = self.env.ref('gs_project_planning.view_planning_slot_graph_gs')
        pivot_view = self.env.ref('gs_project_planning.view_planning_slot_pivot_gs')
        search_view = self.env.ref('gs_project_planning.view_planning_slot_search_gs')
        return {
            'name': _('Planning — %s', self.name),
            'type': 'ir.actions.act_window',
            'res_model': 'planning.slot',
            'view_mode': 'kanban,list,graph,pivot,form',
            'views': [
                (kanban_view.id, 'kanban'),
                (list_view.id, 'list'),
                (graph_view.id, 'graph'),
                (pivot_view.id, 'pivot'),
                (False, 'form'),
            ],
            'search_view_id': search_view.id,
            'domain': [('project_id', '=', self.id)],
            'context': {
                'default_project_id': self.id,
                'search_default_group_by_day': 1,
            },
        }

    def action_open_pending_slots(self):
        self.ensure_one()
        kanban_view = self.env.ref('gs_project_planning.view_planning_slot_kanban_gs')
        list_view = self.env.ref('gs_project_planning.view_planning_slot_list_gs')
        graph_view = self.env.ref('gs_project_planning.view_planning_slot_graph_gs')
        pivot_view = self.env.ref('gs_project_planning.view_planning_slot_pivot_gs')
        search_view = self.env.ref('gs_project_planning.view_planning_slot_search_gs')
        return {
            'name': _('Créneaux à valider — %s', self.name),
            'type': 'ir.actions.act_window',
            'res_model': 'planning.slot',
            'view_mode': 'kanban,list,graph,pivot,form',
            'views': [
                (kanban_view.id, 'kanban'),
                (list_view.id, 'list'),
                (graph_view.id, 'graph'),
                (pivot_view.id, 'pivot'),
                (False, 'form'),
            ],
            'search_view_id': search_view.id,
            'domain': [('project_id', '=', self.id)],
            'context': {
                'default_project_id': self.id,
                'search_default_filter_pending': 1,
                'search_default_group_by_day': 1,
            },
        }

    def action_open_project_timesheets(self):
        """Ouvre uniquement les feuilles de temps issues des slots validés
        pour la paie (pas les saisies manuelles hors planning)."""
        self.ensure_one()
        timesheet_ids = self.env['planning.slot'].search([
            ('project_id', '=', self.id),
            ('is_validated', '=', True),
            ('timesheet_id', '!=', False),
        ]).timesheet_id.ids
        return {
            'name': _('Feuilles de temps validées — %s', self.name),
            'type': 'ir.actions.act_window',
            'res_model': 'account.analytic.line',
            'view_mode': 'list,form',
            'views': [(False, 'list'), (False, 'form')],
            'domain': [('id', 'in', timesheet_ids)],
            'context': {
                'default_project_id': self.id,
                'default_is_timesheet': True,
            },
        }

    def action_validate_all_past_slots(self):
        """Valide en lot tous les shifts passés non encore validés."""
        self.ensure_one()
        if not self.allow_timesheets:
            raise UserError(_(
                "Activez d'abord « Timesheets » dans les paramètres du "
                "projet « %s ».", self.display_name,
            ))
        slots = self.env['planning.slot'].search([
            ('project_id', '=', self.id),
            ('is_validated', '=', False),
            ('is_absent', '=', False),
            ('end_datetime', '<', fields.Datetime.now()),
        ])
        if not slots:
            raise UserError(_("Aucun shift passé en attente de validation."))
        return slots.action_validate_for_payroll()

    def action_generate_planning_slots(self):
        self.ensure_one()
        self._validate_generation_inputs()

        tz_name = (self.env.user.tz
                   or self.company_id.resource_calendar_id.tz
                   or 'Africa/Casablanca')
        local_tz = pytz.timezone(tz_name)

        slots_vals = []
        current = self.date_start
        while current <= self.date:
            for line in self.planning_line_ids:
                if self._is_line_rest_day(line, current):
                    continue
                if not self._is_employee_working_day(line.employee_id, current, local_tz):
                    continue
                slots_vals.append(self._prepare_slot_vals(line, current, local_tz))
            current += timedelta(days=1)

        if not slots_vals:
            raise UserError(_(
                "Aucun créneau n'a pu être généré. Vérifiez que les employés "
                "ont des jours travaillés dans la période du projet."
            ))

        self.env['planning.slot'].create(slots_vals)
        self.planning_generated = True

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Planning généré'),
                'message': _(
                    "%(n)d créneaux créés pour %(e)d employé(s) entre "
                    "%(s)s et %(end)s.",
                    n=len(slots_vals),
                    e=len(self.planning_line_ids),
                    s=self.date_start,
                    end=self.date,
                ),
                'type': 'success',
                'sticky': False,
            },
        }

    def action_refresh_planning(self):
        """Met à jour le planning prévisionnel sur les jours FUTURS uniquement.

        - Ajoute des créneaux pour les nouveaux employés présents dans
          planning_line_ids mais sans slot futur.
        - Supprime les créneaux futurs (non validés, non absents) des employés
          qui ne figurent plus dans planning_line_ids.
        - Les slots passés ou validés ne sont JAMAIS modifiés.

        Pivot = demain 00h00 dans le fuseau horaire de l'utilisateur.
        """
        self.ensure_one()
        if not self.date_start or not self.date:
            raise UserError(_(
                "Le projet doit avoir une Date de début et une Date de fin "
                "pour pouvoir actualiser le planning."
            ))
        if not self.planning_line_ids:
            raise UserError(_(
                "Aucune ligne d'affectation (onglet « Planning Resources »). "
                "Ajoutez au moins un employé avant d'actualiser."
            ))

        tz_name = (self.env.user.tz
                   or self.company_id.resource_calendar_id.tz
                   or 'Africa/Casablanca')
        local_tz = pytz.timezone(tz_name)

        today_local = datetime.now(local_tz).date()
        tomorrow_local_date = today_local + timedelta(days=1)
        tomorrow_local_dt = local_tz.localize(
            datetime.combine(tomorrow_local_date, time.min)
        )
        cutoff_utc = tomorrow_local_dt.astimezone(pytz.UTC).replace(tzinfo=None)

        if tomorrow_local_date > self.date:
            raise UserError(_(
                "Aucun jour futur à actualiser : le projet se termine "
                "aujourd'hui ou avant."
            ))

        Slot = self.env['planning.slot']
        current_employee_ids = self.planning_line_ids.employee_id.ids

        # 1) Supprimer les slots futurs des employés retirés de l'équipe
        removed_slots = Slot.search([
            ('project_id', '=', self.id),
            ('start_datetime', '>=', cutoff_utc),
            ('is_validated', '=', False),
            ('is_absent', '=', False),
            ('employee_id', 'not in', current_employee_ids),
            ('replaces_slot_id', '=', False),
        ])
        removed_count = len(removed_slots)
        removed_slots.unlink()

        # 2) Repérer les slots futurs déjà présents pour ne pas dupliquer
        existing = Slot.search([
            ('project_id', '=', self.id),
            ('start_datetime', '>=', cutoff_utc),
        ])
        existing_keys = {
            (s.employee_id.id, s.start_datetime.date())
            for s in existing if s.employee_id and s.start_datetime
        }

        # 3) Générer les slots manquants pour chaque ligne, de cutoff à date_end
        start_day = max(self.date_start, tomorrow_local_date)
        new_slots_vals = []
        current_day = start_day
        while current_day <= self.date:
            for line in self.planning_line_ids:
                key = (line.employee_id.id, current_day)
                if key in existing_keys:
                    continue
                if self._is_line_rest_day(line, current_day):
                    continue
                if not self._is_employee_working_day(
                    line.employee_id, current_day, local_tz
                ):
                    continue
                new_slots_vals.append(
                    self._prepare_slot_vals(line, current_day, local_tz)
                )
            current_day += timedelta(days=1)

        if new_slots_vals:
            Slot.create(new_slots_vals)
        added_count = len(new_slots_vals)

        self.planning_generated = True
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Planning actualisé'),
                'message': _(
                    "%(added)d créneau(x) ajouté(s), %(removed)d supprimé(s) — "
                    "à partir du %(cutoff)s. Les shifts passés et validés sont "
                    "intacts.",
                    added=added_count,
                    removed=removed_count,
                    cutoff=tomorrow_local_date.strftime('%d/%m/%Y'),
                ),
                'type': 'success',
                'sticky': False,
            },
        }

    def _validate_generation_inputs(self):
        if not self.date_start or not self.date:
            raise UserError(_(
                "Veuillez définir une Date de début et une Date de fin "
                "sur le projet."
            ))
        if self.date_start > self.date:
            raise UserError(_("La date de début doit précéder la date de fin."))
        if not self.planning_line_ids:
            raise UserError(_(
                "Veuillez ajouter au moins une ligne d'affectation "
                "(onglet « Planning Resources »)."
            ))
        if self.planning_generated:
            raise UserError(_(
                "Le planning a déjà été généré pour ce projet. "
                "Utilisez « Réinitialiser le planning » pour le régénérer."
            ))

    def _is_line_rest_day(self, line, day):
        """True si `day` tombe un jour de repos de l'employé pour cette ligne.

        Priorité : jours de repos définis sur la ligne (override projet), sinon
        ceux de la fiche employé. `day.weekday()` : lundi=0 … dimanche=6, ce
        qui correspond au champ `dayofweek` de gs.rest.weekday."""
        # sudo : rest_weekday_ids n'est pas un champ public de l'employé ; la
        # génération peut être lancée par un chef de projet sans droits RH.
        rest_days = line.rest_weekday_ids or line.employee_id.sudo().rest_weekday_ids
        return day.weekday() in rest_days.mapped('dayofweek')

    def _is_employee_working_day(self, employee, day, local_tz):
        calendar = employee.resource_calendar_id or self.company_id.resource_calendar_id
        if not calendar or not employee.resource_id:
            return True
        start_local = local_tz.localize(datetime.combine(day, time.min))
        end_local = local_tz.localize(datetime.combine(day, time.max))
        intervals = calendar._work_intervals_batch(
            start_local.astimezone(pytz.UTC),
            end_local.astimezone(pytz.UTC),
            resources=employee.resource_id,
        )[employee.resource_id.id]
        return bool(intervals)

    def _prepare_slot_vals(self, line, day, local_tz):
        start_h = int(line.start_hour)
        start_m = int(round((line.start_hour - start_h) * 60))
        end_h = int(line.end_hour)
        end_m = int(round((line.end_hour - end_h) * 60))

        local_start = local_tz.localize(datetime.combine(day, time(start_h, start_m)))
        end_day = day + timedelta(days=1) if line.crosses_midnight else day
        local_end = local_tz.localize(datetime.combine(end_day, time(end_h, end_m)))

        return {
            'project_id': self.id,
            'employee_id': line.employee_id.id,
            'resource_id': line.employee_id.resource_id.id,
            'role_id': line.role_id.id,
            'start_datetime': local_start.astimezone(pytz.UTC).replace(tzinfo=None),
            'end_datetime': local_end.astimezone(pytz.UTC).replace(tzinfo=None),
            'company_id': self.company_id.id,
            'state': 'draft',
            'break_duration': line.break_duration or 0.0,
            'is_daily_paid': line.shift_id.is_daily_paid,
        }
