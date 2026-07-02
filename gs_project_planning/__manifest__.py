# -*- coding: utf-8 -*-
{
    'name': "GS Project Planning",
    'summary': "Auto-génération du planning projet + exclusivité employé",
    'description': """
GS Project Planning
===================
- Définit une liste d'employés/rôle/shift par projet
- Génère automatiquement les créneaux de planning sur toute la durée du projet
  lorsque le projet passe au stage "Gagné"
- Empêche qu'un employé soit affecté à 2 projets se chevauchant
- Fournit 3 shifts standards de sécurité (Matin/Après-midi/Nuit),
  un shift Ménage et un shift Manager
    """,
    'author': "AH",
    'website': "https://www.metraco.ma",
    'category': 'Services/Project',
    'version': '18.0.2.25.0',
    'depends': [
        'project',
        'project_forecast',
        'planning',
        'hr',
        'hr_timesheet',
        'hr_payroll',
    ],
    'data': [
        'security/gs_security_groups.xml',
        'security/ir.model.access.csv',
        'data/planning_role_data.xml',
        'data/project_stage_data.xml',
        'data/gs_planning_shift_data.xml',
        'data/sample_data.xml',
        'data/sample_contracts.xml',
        'wizards/gs_planning_replace_wizard_views.xml',
        'wizards/gs_planning_overtime_wizard_views.xml',
        'wizards/gs_planning_import_wizard_views.xml',
        'views/gs_planning_assignment_views.xml',
        'views/gs_planning_actual_views.xml',
        'views/hr_employee_views.xml',
        'views/gs_planning_shift_views.xml',
        'views/project_project_views.xml',
        'views/gs_project_planning_line_views.xml',
        'views/planning_slot_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'gs_project_planning/static/src/scss/*.scss',
            'gs_project_planning/static/src/views/*.js',
            'gs_project_planning/static/src/views/*.xml',
        ],
    },
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
