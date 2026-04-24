from odoo import api, fields, models, tools


class AccountAssetReport(models.Model):
    _name = 'account.asset.report'
    _description = 'Asset Analysis Report'
    _auto = False

    # Grouping Fields
    asset_id = fields.Many2one('account.asset', string='Asset', readonly=True)
    name = fields.Char(string='Asset Name', readonly=True)
    company_id = fields.Many2one('res.company', string='Company', readonly=True)
    currency_id = fields.Many2one('res.currency', string='Currency', readonly=True)
    asset_group_id = fields.Many2one('account.asset.group', string='Asset Group', readonly=True)
    method = fields.Selection([
        ('linear', 'Straight Line'),
        ('degressive', 'Declining'),
        ('degressive_then_linear', 'Declining then Straight Line')
    ], string='Computation Method', readonly=True)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('open', 'Running'),
        ('paused', 'On Hold'),
        ('close', 'Closed')
    ], string='Status', readonly=True)
    acquisition_date = fields.Date(string='Acquisition Date', readonly=True)
    prorata_date = fields.Date(string='Prorata Date', readonly=True)

    # Measure Fields
    original_value = fields.Monetary(string='Original Value', readonly=True)
    book_value = fields.Monetary(string='Book Value', readonly=True)
    salvage_value = fields.Monetary(string='Not Depreciable Value', readonly=True)
    depreciated_value = fields.Monetary(string='Cumulative Depreciation', readonly=True)

    def _select(self):
        return """
            SELECT 
                a.id as id,
                a.id as asset_id,
                a.name as name,
                a.company_id as company_id,
                c.currency_id as currency_id,
                a.asset_group_id as asset_group_id,
                a.method as method,
                a.state as state,
                a.acquisition_date as acquisition_date,
                a.prorata_date as prorata_date,
                a.original_value as original_value,
                a.book_value as book_value,
                a.salvage_value as salvage_value,
                (a.original_value - a.book_value) as depreciated_value
        """

    def _from(self):
        return """
            FROM account_asset a
            LEFT JOIN res_company c ON a.company_id = c.id
        """

    def _where(self):
        return """
            WHERE a.state != 'model' 
              AND a.state != 'cancelled'
        """

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute("""
            CREATE OR REPLACE VIEW %s AS (
                %s
                %s
                %s
            )
        """ % (self._table, self._select(), self._from(), self._where()))
