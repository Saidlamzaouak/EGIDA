# -*- coding: utf-8 -*-
{
    'name': "GS Project Planning",
    'summary': "Generate planning slots for project employees based on project period",
    'description': """
        This module allows users to assign employees to a project and automatically 
        generate planning slots for the entire duration of the project.
    """,
    'author': "AH",
    'website': "https://www.yourcompany.com",
    'category': 'Project',
    'version': '1.0',
    'depends': ['project', 'project_forecast', 'planning', 'hr'],
    'data': [
        'views/project_project_views.xml',
    ],
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
