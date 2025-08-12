from odoo import models, fields

class CouvertureStock(models.Model):
    _name = 'couverture.stock'
    _description = 'Couverture de Stock'

    company_id = fields.Many2one('res.company', string='Magasin')
    company_name = fields.Char(string='Nom Société', default=lambda self: self.env.company.name)

    product_id = fields.Many2one('product.product', string="Produit")
    product_barcode = fields.Char(string="Code-barres")
    product_name = fields.Char(string="Nom produit")
    total_en_stock = fields.Float(string="Stock actuel")
    total_vendu = fields.Float(string="Total vendu")
    vmj = fields.Float(string="Vente moyenne journalière")
    couverture_stock_en_jours = fields.Float(string="Couverture de stock")
    couverture_cible = fields.Integer(string="Cible ( en jours)")
    marge_livraison = fields.Integer(string="Delais de livraison(en jours)")
    qte_a_commander = fields.Float(string="Qté à commander")
    location_name = fields.Char(string="Entrepôt")

