{
    'name': 'KADERIM Stock Report',
    'version': '1.0',
    'category': 'Inventory',
    'summary': 'Analyse de couverture de stock avec calcul de VMJ',
    'author': 'Kaderim',
    'depends': ['stock'],
    'data': [
        'security/ir.model.access.csv',
        'views/menu/menu.xml',
        'views/couverture_stock_views.xml',
        'views/couverture_stock_wizard_views.xml',
    ],
    'installable': True,
    'application': True,
}
