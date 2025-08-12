from odoo import models, fields, api
from odoo.exceptions import UserError
from datetime import timedelta
import io
import base64
import xlsxwriter


class CouvertureStockWizard(models.TransientModel):
    _name = 'couverture.stock.wizards'
    _description = 'Assistant de calcul de couverture de stock'

    date_start = fields.Datetime(string="Date début", required=True)
    date_end = fields.Datetime(string="Date fin", required=True)
    couverture_cible = fields.Integer(string="Couverture cible (jours)")
    marge_livraison = fields.Integer(string="Marge de livraison (jours)")

    company_id = fields.Many2one(
        'res.company',
        string='Societe',
        required=True,
        readonly=True,
        default=lambda self: self.env.company
    )

    auto_recalcul = fields.Boolean(
        string="Recalcul automatique",
        default=True,
        help="Recalcule automatiquement quand la société ou les paramètres changent"
    )

    update_mode = fields.Selection([
        ('replace', 'Remplacer toutes les données'),
        ('smart', 'Mise à jour intelligente (recommandé)')
    ], string="Mode de mise à jour", default='smart',
        help="Remplacer: supprime tout et recrée\nIntelligente: met à jour seulement les changements")

    @api.onchange('date_start', 'date_end')
    def _onchange_set_default_couverture_cible(self):
        """Met automatiquement à jour la couverture cible selon la période choisie."""
        if self.date_start and self.date_end and self.date_start <= self.date_end:
            self.couverture_cible = (self.date_end - self.date_start).days

    @api.onchange('company_id')
    def _onchange_company_id(self):
        if self.auto_recalcul and self.company_id and self.date_start and self.date_end:
            self._auto_recalcul()

    @api.onchange('date_start', 'date_end', 'couverture_cible')
    def _onchange_dates_or_target(self):
        if self.auto_recalcul and self.company_id and self.date_start and self.date_end:
            self._auto_recalcul()

    @api.onchange('marge_livraison')
    def _onchange_marge_livraison(self):
        if self.auto_recalcul and self.company_id and self.date_start and self.date_end:
            self._auto_recalcul()

    def _auto_recalcul(self):
        try:
            if not self.date_start or not self.date_end or not self.company_id:
                return
            if self.date_start > self.date_end:
                return {
                    'warning': {
                        'title': 'Dates invalides',
                        'message': 'La date de début doit être antérieure à la date de fin.'
                    }
                }
            if self.update_mode == 'smart':
                self._perform_smart_update()
            else:
                self._perform_calculation()
        except Exception:
            pass

    def _perform_calculation(self):
        domain = []
        if hasattr(self.env['couverture.stock'], 'company_id'):
            domain = [('company_id', '=', self.company_id.id)]

        existing_records = self.env['couverture.stock'].search(domain)
        if existing_records:
            existing_records.unlink()

        company_id = self.company_id.id
        delta_days = max(1, (self.date_end - self.date_start).days)

        query = """
            WITH ventes_par_produit AS (
                SELECT 
                    sm.product_id, 
                    SUM(sm.product_uom_qty) AS total_vendu
                FROM stock_move sm
                JOIN stock_picking_type spt ON sm.picking_type_id = spt.id
                WHERE sm.state = 'done'
                  AND spt.code = 'outgoing'
                  AND sm.date BETWEEN %s AND %s
                  AND sm.company_id = %s
                  AND sm.location_id NOT IN (52, 82)
                  AND sm.location_dest_id NOT IN (52, 82)
                GROUP BY sm.product_id
            )
            SELECT 
                pp.id AS product_id,
                pt.name AS product_name,
                pp.barcode AS product_barcode,
                rc.id AS company_id,
                rc.name AS company_name,
                COALESCE(SUM(sq.quantity), 0) AS total_en_stock,
                COALESCE(v.total_vendu, 0) AS total_vendu,
                ROUND(COALESCE(v.total_vendu, 0)::numeric / %s, 2) AS vmj,
                CASE 
                    WHEN COALESCE(v.total_vendu, 0) = 0 THEN 0
                    ELSE ROUND(COALESCE(SUM(sq.quantity), 0)::numeric / (COALESCE(v.total_vendu, 0)::numeric / %s), 2)
                END AS couverture_stock_en_jours,
                %s AS couverture_cible,
                string_agg(DISTINCT sl.complete_name, ', ') AS location_name
            FROM product_product pp
            JOIN product_template pt ON pt.id = pp.product_tmpl_id
            JOIN res_company rc ON rc.id = %s
            LEFT JOIN stock_quant sq ON sq.product_id = pp.id AND sq.company_id = rc.id AND sq.location_id IN (
                SELECT id FROM stock_location 
                WHERE usage = 'internal' 
                  AND company_id = rc.id
                  AND id NOT IN (52, 82)
            )
            LEFT JOIN stock_location sl ON sl.id = sq.location_id
            LEFT JOIN ventes_par_produit v ON v.product_id = pp.id
            WHERE pt.active = TRUE
              AND pt.type = 'product'
            GROUP BY pp.id, pt.name, pp.barcode, v.total_vendu, rc.id, rc.name
            ORDER BY pt.name
        """

        self.env.cr.execute(query, (
            self.date_start,
            self.date_end,
            company_id,
            delta_days,
            delta_days,
            self.couverture_cible,
            company_id
        ))

        rows = self.env.cr.fetchall()
        columns = [desc[0] for desc in self.env.cr.description]
        lang = self.env.lang or 'fr_FR'

        records = []
        for row in rows:
            data = dict(zip(columns, row))
            name = data.get('product_name')
            if isinstance(name, dict):
                data['product_name'] = name.get(lang) or next(iter(name.values()))

            vmj = data['vmj'] or 0
            stock = data.get('total_en_stock') or 0
            cible = self.couverture_cible or 0
            marge = vmj * (self.marge_livraison or 0)

            if stock > marge:
                qte = (vmj * cible) - (stock - marge)
            else:
                qte = vmj * cible

            data['qte_a_commander'] = max(0, round(qte, 2))
            data['marge_livraison'] = self.marge_livraison or 0

            records.append(data)

        if records:
            self.env['couverture.stock'].create(records)

    def _perform_smart_update(self):
        company_id = self.company_id.id
        delta_days = max(1, (self.date_end - self.date_start).days)

        query = """
            WITH ventes_par_produit AS (
                SELECT 
                    sm.product_id, 
                    SUM(sm.product_uom_qty) AS total_vendu
                FROM stock_move sm
                JOIN stock_picking_type spt ON sm.picking_type_id = spt.id
                WHERE sm.state = 'done'
                  AND spt.code = 'outgoing'
                  AND sm.date BETWEEN %s AND %s
                  AND sm.company_id = %s
                  AND sm.location_id NOT IN (52, 82)
                  AND sm.location_dest_id NOT IN (52, 82)
                GROUP BY sm.product_id
            )
            SELECT 
                pp.id AS product_id,
                pt.name AS product_name,
                pp.barcode AS product_barcode,
                rc.id AS company_id,
                rc.name AS company_name,
                COALESCE(SUM(sq.quantity), 0) AS total_en_stock,
                COALESCE(v.total_vendu, 0) AS total_vendu,
                ROUND(COALESCE(v.total_vendu, 0)::numeric / %s, 2) AS vmj,
                CASE 
                    WHEN COALESCE(v.total_vendu, 0) = 0 THEN 0
                    ELSE ROUND(COALESCE(SUM(sq.quantity), 0)::numeric / (COALESCE(v.total_vendu, 0)::numeric / %s), 2)
                END AS couverture_stock_en_jours,
                %s AS couverture_cible,
                string_agg(DISTINCT sl.complete_name, ', ') AS location_name
            FROM product_product pp
            JOIN product_template pt ON pt.id = pp.product_tmpl_id
            JOIN res_company rc ON rc.id = %s
            LEFT JOIN stock_quant sq ON sq.product_id = pp.id AND sq.company_id = rc.id AND sq.location_id IN (
                SELECT id FROM stock_location 
                WHERE usage = 'internal' 
                  AND company_id = rc.id
                  AND id NOT IN (52, 82)
            )
            LEFT JOIN stock_location sl ON sl.id = sq.location_id
            LEFT JOIN ventes_par_produit v ON v.product_id = pp.id
            WHERE pt.active = TRUE
              AND pt.type = 'product'
            GROUP BY pp.id, pt.name, pp.barcode, v.total_vendu, rc.id, rc.name
            ORDER BY pt.name
        """

        self.env.cr.execute(query, (
            self.date_start,
            self.date_end,
            company_id,
            delta_days,
            delta_days,
            self.couverture_cible,
            company_id
        ))

        rows = self.env.cr.fetchall()
        columns = [desc[0] for desc in self.env.cr.description]
        lang = self.env.lang or 'fr_FR'

        existing_domain = [('company_id', '=', company_id)]
        existing_records = self.env['couverture.stock'].search(existing_domain)
        existing_by_product = {rec.product_id.id: rec for rec in existing_records if hasattr(rec, 'product_id')}

        records_to_create = []
        records_to_update = []

        for row in rows:
            data = dict(zip(columns, row))
            name = data.get('product_name')
            if isinstance(name, dict):
                data['product_name'] = name.get(lang) or next(iter(name.values()))

            vmj = data['vmj'] or 0
            stock = data.get('total_en_stock') or 0
            cible = self.couverture_cible or 0
            marge = vmj * (self.marge_livraison or 0)

            if stock > marge:
                qte = (vmj * cible) - (stock - marge)
            else:
                qte = vmj * cible

            data['qte_a_commander'] = max(0, round(qte, 2))
            data['marge_livraison'] = self.marge_livraison or 0

            product_id = data.get('product_id')
            existing_record = existing_by_product.get(product_id)

            if existing_record:
                existing_record.write(data)
                records_to_update.append(existing_record.id)
            else:
                records_to_create.append(data)

        if records_to_create:
            self.env['couverture.stock'].create(records_to_create)

        updated_product_ids = [row[0] for row in rows]
        records_to_delete = existing_records.filtered(
            lambda r: hasattr(r, 'product_id') and r.product_id.id not in updated_product_ids
        )
        if records_to_delete:
            records_to_delete.unlink()

    def action_lancer_calcul(self):
        self.ensure_one()

        if self.date_start > self.date_end:
            raise UserError("La date de début doit être antérieure à la date de fin.")

        if self.update_mode == 'smart':
            self._perform_smart_update()
        else:
            self._perform_calculation()

        return {
            'type': 'ir.actions.act_window',
            'name': f'Couverture Stock - {self.company_id.name}',
            'res_model': 'couverture.stock',
            'view_mode': 'tree,pivot,graph',
            'target': 'current',
            'context': {
                'default_company_id': self.company_id.id,
                'search_default_company_id': self.company_id.id,
            }
        }

    def action_voir_resultats(self):
        self.ensure_one()

        domain = [('company_id', '=', self.company_id.id)] if hasattr(self.env['couverture.stock'], 'company_id') else []
        existing_records = self.env['couverture.stock'].search(domain)

        if existing_records:
            date_start = self.date_start
            date_end = self.date_end
            couverture_cible = self.couverture_cible
            update_mode = self.update_mode
            company_id = self.company_id
            marge_livraison = self.marge_livraison

            existing_records.unlink()

            self.write({
                'date_start': date_start,
                'date_end': date_end,
                'couverture_cible': couverture_cible,
                'update_mode': update_mode,
                'company_id': company_id.id,
                'marge_livraison': marge_livraison,
            })

            if update_mode == 'smart':
                self._perform_smart_update()
            else:
                self._perform_calculation()
        else:
            if self.update_mode == 'smart':
                self._perform_smart_update()
            else:
                self._perform_calculation()

        return {
            'type': 'ir.actions.act_window',
            'name': f'Couverture Stock - {self.company_id.name}',
            'res_model': 'couverture.stock',
            'view_mode': 'tree,pivot,graph',
            'target': 'current',
            'context': {
                'default_company_id': self.company_id.id,
                'search_default_company_id': self.company_id.id,
            },
            'domain': [('company_id', '=', self.company_id.id)],
        }

    def export_xlsx(self):
        buffer = io.BytesIO()
        workbook = xlsxwriter.Workbook(buffer)
        worksheet = workbook.add_worksheet("Couverture Stock")

        headers = ['Produit', 'Code Barre', 'Stock', 'Total Vendu', 'VMJ', 'Couverture (jours)', 'Qté à Commander']
        for col_num, header in enumerate(headers):
            worksheet.write(0, col_num, header)

        records = self.env['couverture.stock'].search([('company_id', '=', self.company_id.id)])

        for row_num, record in enumerate(records, start=1):
            worksheet.write(row_num, 0, record.product_name)
            worksheet.write(row_num, 1, record.product_barcode)
            worksheet.write(row_num, 2, record.total_en_stock)
            worksheet.write(row_num, 3, record.total_vendu)
            worksheet.write(row_num, 4, record.vmj)
            worksheet.write(row_num, 5, record.couverture_stock_en_jours)
            worksheet.write(row_num, 6, record.qte_a_commander)

        workbook.close()
        buffer.seek(0)

        export_data = base64.b64encode(buffer.read())

        filename = f"Couverture_Stock_{self.company_id.name}.xlsx"

        export_file = self.env['ir.attachment'].create({
            'name': filename,
            'type': 'binary',
            'datas': export_data,
            'res_model': self._name,
            'res_id': self.id,
            'mimetype': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        })

        return {
            'type': 'ir.actions.act_url',
            'url': f'/web/content/{export_file.id}?download=true',
            'target': 'self',
        }
