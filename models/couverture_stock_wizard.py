from odoo import models, fields, api
from odoo.exceptions import UserError
from datetime import timedelta


class CouvertureStockWizard(models.TransientModel):
    _name = 'couverture.stock.wizards'
    _description = 'Assistant de calcul de couverture de stock'

    date_start = fields.Datetime(string="Date début", required=True)
    date_end = fields.Datetime(string="Date fin", required=True)
    couverture_cible = fields.Integer(string="Couverture cible (jours)", default=30)

    company_id = fields.Many2one(
        'res.company',
        string='Societe',
        required=True,
        default=lambda self: self.env.company
    )

    # Champ pour déclencher le recalcul automatique
    auto_recalcul = fields.Boolean(
        string="Recalcul automatique",
        default=True,
        help="Recalcule automatiquement quand la société ou les paramètres changent"
    )

    # Champ pour choisir le mode de mise à jour
    update_mode = fields.Selection([
        ('replace', 'Remplacer toutes les données'),
        ('smart', 'Mise à jour intelligente (recommandé)')
    ], string="Mode de mise à jour", default='smart',
        help="Remplacer: supprime tout et recrée\nIntelligente: met à jour seulement les changements")

    @api.onchange('company_id')
    def _onchange_company_id(self):
        """Déclenche automatiquement le recalcul quand la société change"""
        if self.auto_recalcul and self.company_id and self.date_start and self.date_end:
            self._auto_recalcul()

    @api.onchange('date_start', 'date_end', 'couverture_cible')
    def _onchange_dates_or_target(self):
        """Recalcule aussi quand les dates ou la cible changent"""
        if self.auto_recalcul and self.company_id and self.date_start and self.date_end:
            self._auto_recalcul()

    def _auto_recalcul(self):
        """Méthode privée pour le recalcul automatique"""
        try:
            # Validation basique
            if not self.date_start or not self.date_end or not self.company_id:
                return

            if self.date_start > self.date_end:
                return {
                    'warning': {
                        'title': 'Dates invalides',
                        'message': 'La date de début doit être antérieure à la date de fin.'
                    }
                }

            # Lancer le calcul automatiquement (sans transaction commit)
            if self.update_mode == 'smart':
                self._perform_smart_update()
            else:
                self._perform_calculation()

        except Exception as e:
            # En cas d'erreur, on ne fait rien pour éviter de bloquer l'interface
            pass

    def _perform_calculation(self):
        """Méthode privée pour effectuer le calcul"""
        # Suppression sélective : seulement les données de la société courante
        domain = []
        if hasattr(self.env['couverture.stock'], 'company_id'):
            domain = [('company_id', '=', self.company_id.id)]
        else:
            # Si pas de champ company_id, supprimer toutes les données
            domain = []

        existing_records = self.env['couverture.stock'].search(domain)
        if existing_records:
            existing_records.unlink()

        # Company ID
        company_id = self.company_id.id

        # Calcul jours entre les dates, inclusif
        delta_days = max(1, (self.date_end - self.date_start).days + 1)

    def _perform_calculation(self):
        """Méthode privée pour effectuer le calcul"""
        # Suppression sélective : seulement les données de la société courante
        domain = []
        if hasattr(self.env['couverture.stock'], 'company_id'):
            domain = [('company_id', '=', self.company_id.id)]
        else:
            # Si pas de champ company_id, supprimer toutes les données
            domain = []

        existing_records = self.env['couverture.stock'].search(domain)
        if existing_records:
            existing_records.unlink()

        # Company ID
        company_id = self.company_id.id

        # Calcul jours entre les dates, inclusif
        delta_days = max(1, (self.date_end - self.date_start).days + 1)

        query = """
            WITH ventes_par_produit AS (
                SELECT sm.product_id, SUM(sm.product_uom_qty) AS total_vendu
                FROM stock_move sm
                JOIN stock_location src ON src.id = sm.location_id
                WHERE sm.state = 'done'
                  AND sm.date BETWEEN %s AND %s
                  AND sm.picking_type_id IN (
                      SELECT id FROM stock_picking_type WHERE code = 'outgoing'
                  )
                  AND src.company_id = %s  -- Filtrer les ventes par société
                GROUP BY sm.product_id
            )
            SELECT 
                sq.product_id,
                pt.name AS product_name,
                pp.barcode AS product_barcode,
                sl.company_id,  -- ID de la société
                rc.name AS company_name,  -- Nom de la société
                SUM(sq.quantity) AS total_en_stock,
                COALESCE(v.total_vendu, 0) AS total_vendu,
                ROUND(COALESCE(v.total_vendu, 0)::numeric / %s, 2) AS vmj,
                CASE 
                    WHEN COALESCE(v.total_vendu, 0) = 0 THEN 0
                    ELSE ROUND(SUM(sq.quantity)::numeric / (COALESCE(v.total_vendu, 0)::numeric / %s), 2)
                END AS couverture_stock_en_jours,
                %s AS couverture_cible,
                sl.complete_name AS location_name
            FROM stock_quant sq
            JOIN product_product pp ON pp.id = sq.product_id
            JOIN product_template pt ON pt.id = pp.product_tmpl_id
            JOIN stock_location sl ON sl.id = sq.location_id
            JOIN res_company rc ON rc.id = sl.company_id
            LEFT JOIN ventes_par_produit v ON v.product_id = sq.product_id
            WHERE sl.company_id = %s  -- Filtrer les stocks par société
              AND sl.usage = 'internal'  -- Seulement les emplacements de stock réel
              AND sq.quantity > 0  -- Éviter les quantités nulles/négatives
            GROUP BY sq.product_id, pt.name, pp.barcode, v.total_vendu, sl.complete_name, sl.company_id, rc.name
            ORDER BY pt.name
        """

        self.env.cr.execute(query, (
            self.date_start,
            self.date_end,
            company_id,  # Pour filtrer les ventes
            delta_days,
            delta_days,
            self.couverture_cible,
            company_id  # Pour filtrer les stocks
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

            data['qte_a_commander'] = max(0, round((data['vmj'] * self.couverture_cible) - (data['total_en_stock'] - (data['vmj'] * 4)), 2))
            records.append(data)

        if records:
            self.env['couverture.stock'].create(records)

    def _perform_smart_update(self):
        """Méthode alternative pour une mise à jour intelligente (upsert)"""
        # Company ID
        company_id = self.company_id.id

        # Calcul jours entre les dates, inclusif
        delta_days = max(1, (self.date_end - self.date_start).days + 1)

        # Même requête que _perform_calculation
        query = """
            WITH ventes_par_produit AS (
                SELECT sm.product_id, SUM(sm.product_uom_qty) AS total_vendu
                FROM stock_move sm
                JOIN stock_location src ON src.id = sm.location_id
                WHERE sm.state = 'done'
                  AND sm.date BETWEEN %s AND %s
                  AND sm.picking_type_id IN (
                      SELECT id FROM stock_picking_type WHERE code = 'outgoing'
                  )
                  AND src.company_id = %s
                GROUP BY sm.product_id
            )
            SELECT 
                sq.product_id,
                pt.name AS product_name,
                pp.barcode AS product_barcode,
                sl.company_id,
                rc.name AS company_name,
                SUM(sq.quantity) AS total_en_stock,
                COALESCE(v.total_vendu, 0) AS total_vendu,
                ROUND(COALESCE(v.total_vendu, 0)::numeric / %s, 2) AS vmj,
                CASE 
                    WHEN COALESCE(v.total_vendu, 0) = 0 THEN 0
                    ELSE ROUND(SUM(sq.quantity)::numeric / (COALESCE(v.total_vendu, 0)::numeric / %s), 2)
                END AS couverture_stock_en_jours,
                %s AS couverture_cible,
                sl.complete_name AS location_name
            FROM stock_quant sq
            JOIN product_product pp ON pp.id = sq.product_id
            JOIN product_template pt ON pt.id = pp.product_tmpl_id
            JOIN stock_location sl ON sl.id = sq.location_id
            JOIN res_company rc ON rc.id = sl.company_id
            LEFT JOIN ventes_par_produit v ON v.product_id = sq.product_id
            WHERE sl.company_id = %s
              AND sl.usage = 'internal'
              AND sq.quantity > 0
            GROUP BY sq.product_id, pt.name, pp.barcode, v.total_vendu, sl.complete_name, sl.company_id, rc.name
            ORDER BY pt.name
        """

        self.env.cr.execute(query, (
            self.date_start, self.date_end, company_id,
            delta_days, delta_days, self.couverture_cible, company_id
        ))

        rows = self.env.cr.fetchall()
        columns = [desc[0] for desc in self.env.cr.description]
        lang = self.env.lang or 'fr_FR'

        # Récupérer les enregistrements existants pour cette société
        existing_domain = []
        if hasattr(self.env['couverture.stock'], 'company_id'):
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

            data['qte_a_commander'] = max(0, round((data['vmj'] * self.couverture_cible) - (data['total_en_stock'] - (data['vmj'] * 4)), 2))

            product_id = data.get('product_id')
            existing_record = existing_by_product.get(product_id)

            if existing_record:
                # Mettre à jour l'enregistrement existant
                existing_record.write(data)
                records_to_update.append(existing_record.id)
            else:
                # Créer un nouveau enregistrement
                records_to_create.append(data)

        # Créer les nouveaux enregistrements
        if records_to_create:
            self.env['couverture.stock'].create(records_to_create)

        # Supprimer les enregistrements qui n'existent plus dans les nouvelles données
        updated_product_ids = [row[0] for row in rows]  # product_id est la première colonne
        records_to_delete = existing_records.filtered(
            lambda r: hasattr(r, 'product_id') and r.product_id.id not in updated_product_ids
        )
        if records_to_delete:
            records_to_delete.unlink()

    def action_lancer_calcul(self):
        """Action manuelle pour lancer le calcul"""
        self.ensure_one()

        # Validation des dates
        if self.date_start > self.date_end:
            raise UserError("La date de début doit être antérieure à la date de fin.")

        # Effectuer le calcul selon le mode choisi
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
        """Voir les résultats existants ou recalculer selon le cas."""
        self.ensure_one()

        # Filtrer les données existantes pour la société courante
        domain = [('company_id', '=', self.company_id.id)] if hasattr(self.env['couverture.stock'],
                                                                      'company_id') else []
        existing_records = self.env['couverture.stock'].search(domain)

        if existing_records:
            # Il y a déjà des données — on remplace
            # On conserve les paramètres du wizard
            date_start = self.date_start
            date_end = self.date_end
            couverture_cible = self.couverture_cible
            update_mode = self.update_mode
            company_id = self.company_id

            # Supprimer les anciennes données de cette société
            existing_records.unlink()

            # Réappliquer les paramètres au wizard (utile si le recalcul modifie le wizard)
            self.write({
                'date_start': date_start,
                'date_end': date_end,
                'couverture_cible': couverture_cible,
                'update_mode': update_mode,
                'company_id': company_id.id,
            })

            # Recalculer avec remplacement
            if update_mode == 'smart':
                self._perform_smart_update()
            else:
                self._perform_calculation()

        else:
            # Aucune donnée existante : on fait un calcul normal
            if self.update_mode == 'smart':
                self._perform_smart_update()
            else:
                self._perform_calculation()

        # Afficher la vue des résultats
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
