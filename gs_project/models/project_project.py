# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError
from datetime import timedelta
import pytz
from datetime import datetime, time


class ProjectProject(models.Model):
    _inherit = 'project.project'

    employee_ids = fields.Many2many(
        'hr.employee', 
        'project_employee_rel', 
        'project_id', 
        'employee_id', 
        string='Assigned Employees'
    )

    def action_generate_planning_slots(self):
        self.ensure_one()
        if not self.date_start or not self.date:
            raise UserError(_("Please define both Start Date and End Date for the project."))
        
        if not self.employee_ids:
            raise UserError(_("Please assign at least one employee to the project."))

        PlanningSlot = self.env['planning.slot']
        
        start_date = self.date_start
        end_date = self.date
        
        if start_date > end_date:
            raise UserError(_("Start Date cannot be after End Date."))




        # Get the user's timezone or default to Morocco
        user_tz = self.env.user.tz or 'Africa/Casablanca'
        local_tz = pytz.timezone(user_tz)

        for employee in self.employee_ids:
            # Find the first working day for this specific employee starting from the project start date
            calendar = employee.resource_calendar_id or self.company_id.resource_calendar_id
            search_start = pytz.utc.localize(fields.Datetime.to_datetime(start_date))
            search_end = search_start + timedelta(days=14) # Search within the next 2 weeks
            
            work_intervals = calendar._work_intervals_batch(
                search_start,
                search_end, 
                resources=employee.resource_id
            )[employee.resource_id.id]
            
            actual_start_date = start_date
            if work_intervals:
                # Intervals are returned in UTC, we take the date part of the first one
                first_interval = list(work_intervals)[0]
                actual_start_date = first_interval[0].date()

            # Calculate localized 08:00 and 17:00 for the actual start date, then convert to UTC
            local_start_dt = local_tz.localize(datetime.combine(actual_start_date, time(8, 0, 0)))
            local_end_dt = local_tz.localize(datetime.combine(actual_start_date, time(17, 0, 0)))
            
            utc_start_dt = local_start_dt.astimezone(pytz.UTC).replace(tzinfo=None)
            utc_end_dt = local_end_dt.astimezone(pytz.UTC).replace(tzinfo=None)

            # Avoid duplicate slots for the same employee and project
            domain = [
                ('project_id', '=', self.id),
                ('resource_id', '=', employee.resource_id.id),
            ]
            if 'employee_id' in PlanningSlot._fields:
                domain.append(('employee_id', '=', employee.id))

            existing = PlanningSlot.search(domain, limit=1)

            if not existing:
                vals = {
                    'project_id': self.id,
                    'start_datetime': utc_start_dt,
                    'end_datetime': utc_end_dt,
                    'resource_id': employee.resource_id.id,
                    'company_id': self.company_id.id,
                    'repeat': True,
                    'repeat_interval': 1,
                    'repeat_unit': 'day',
                    'repeat_type': 'until',
                    'repeat_until': end_date,
                }
                if 'employee_id' in PlanningSlot._fields:
                    vals['employee_id'] = employee.id

                PlanningSlot.create(vals)
            
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Planning Generated'),
                'message': _('Successfully generated recurring planning slots (09:00 - 18:00) for assigned employees.'),
                'type': 'success',
                'sticky': False,
            }
        }
