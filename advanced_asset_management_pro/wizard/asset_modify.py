# -*- coding: utf-8 -*-
import datetime
from dateutil.relativedelta import relativedelta

from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.tools import float_compare, float_is_zero


class AssetModify(models.TransientModel):
    _name = 'asset.modify'
    _description = 'Modify Asset'

    name = fields.Char(string='Reason', required=True, default='')
    asset_id = fields.Many2one(
        'account.asset', string='Asset', required=True)
    modify_action = fields.Selection(
        selection=[
            ('dispose', 'Dispose'),
            ('sell', 'Sell'),
            ('modify', 'Re-evaluate'),
            ('pause', 'Pause'),
        ],
        string='Action',
        required=True,
        default='dispose')
    is_resume = fields.Boolean(string="Is Resume", default=False)

    # Re-evaluate fields
    method_number = fields.Integer(
        string='Duration', default=5)
    method_period = fields.Selection(
        [('1', 'Months'), ('12', 'Years')],
        string='Number of Months in a Period', default='12')
    method = fields.Selection(
        [('linear', 'Straight Line'),
         ('degressive', 'Declining'),
         ('degressive_then_linear', 'Declining then Straight Line')],
        string='Method', default='linear')
    method_progress_factor = fields.Float(
        string='Declining Factor', default=0.3)
    value_residual = fields.Monetary(string='Depreciable Amount')
    salvage_value = fields.Monetary(string='Not Depreciable Amount')

    # Disposal fields
    loss_account_id = fields.Many2one(
        'account.account', string='Loss Account',
        domain="[('deprecated', '=', False), ('company_id', '=', company_id)]",
        help="Account used to record the loss on disposal")
    invoice_ids = fields.Many2many(
        'account.move', string='Customer Invoices',
        domain="[('move_type', '=', 'out_invoice'), ('state', '=', 'posted')]",
        help="Linked customer invoices for a sale of the asset")
    gain_account_id = fields.Many2one(
        'account.account', string='Gain Account',
        domain="[('deprecated', '=', False), ('company_id', '=', company_id)]",
        help="Account used to record the gain on sale")

    # Sale fields
    date = fields.Date(
        string='Date', default=fields.Date.today)

    company_id = fields.Many2one(
        related='asset_id.company_id')
    currency_id = fields.Many2one(
        related='asset_id.currency_id')

    invoice_line_ids = fields.Many2many(
        'account.move.line', string='Invoice Lines',
        help="Specific product lines from the selected customer invoices for the sale of the asset.")

    account_asset_id = fields.Many2one(
        'account.account', string='Gross Increase Account',
        domain="[('deprecated', '=', False), ('company_id', '=', company_id)]")
    account_asset_counterpart_id = fields.Many2one(
        'account.account', string='Asset Counterpart Account',
        domain="[('deprecated', '=', False), ('company_id', '=', company_id)]")
    account_depreciation_id = fields.Many2one(
        'account.account', string='Depreciation Account',
        domain="[('deprecated', '=', False), ('company_id', '=', company_id)]")
    account_depreciation_expense_id = fields.Many2one(
        'account.account', string='Expense Account',
        domain="[('deprecated', '=', False), ('company_id', '=', company_id)]")

    @api.onchange('asset_id')
    def _onchange_asset_id(self):
        asset = self.asset_id
        if asset:
            self.method_number = asset.method_number
            self.method_period = asset.method_period
            self.method = asset.method
            self.method_progress_factor = asset.method_progress_factor
            self.value_residual = asset.value_residual
            self.salvage_value = asset.salvage_value
            self.account_asset_id = asset.account_asset_id
            self.account_asset_counterpart_id = asset.account_asset_counterpart_id
            self.account_depreciation_id = asset.account_depreciation_id
            self.account_depreciation_expense_id = asset.account_depreciation_expense_id

    @api.onchange('invoice_ids')
    def _onchange_invoice_ids(self):
        if self.invoice_ids:
            self.invoice_line_ids = [(6, 0, self.invoice_ids.mapped('invoice_line_ids').filtered(
                lambda l: l.display_type == 'product' or (not l.display_type and l.exclude_from_invoice_tab == False)
            ).ids)]
        else:
            self.invoice_line_ids = [(5, 0, 0)]

    def modify(self):
        if self.is_resume:
            return self._resume_asset()
        elif self.modify_action == 'dispose':
            return self._dispose_asset()
        elif self.modify_action == 'sell':
            return self._sell_asset()
        elif self.modify_action == 'modify':
            return self._modify_asset()
        elif self.modify_action == 'pause':
            return self._pause_asset()

    def _dispose_asset(self):
        self.ensure_one()
        asset = self.asset_id
        return asset.set_to_close(
            self.env['account.move.line'],
            date=self.date,
            message=self.name,
            loss_account_id=self.loss_account_id)

    def _sell_asset(self):
        self.ensure_one()
        asset = self.asset_id
        if not self.invoice_line_ids:
            raise UserError(_("Please select at least one customer invoice line for the sale."))
            
        return asset.set_to_close(
            self.invoice_line_ids,
            date=self.date,
            message=self.name,
            gain_account_id=self.gain_account_id)

    def _modify_asset(self):
        self.ensure_one()
        asset = self.asset_id
        values = {}
        if self.method_number != asset.method_number:
            values['method_number'] = self.method_number
        if self.method_period != asset.method_period:
            values['method_period'] = self.method_period
        if self.method != asset.method:
            values['method'] = self.method
        if self.account_asset_id != asset.account_asset_id:
            values['account_asset_id'] = self.account_asset_id.id
        if self.account_asset_counterpart_id != asset.account_asset_counterpart_id:
            values['account_asset_counterpart_id'] = self.account_asset_counterpart_id.id
        if self.account_depreciation_id != asset.account_depreciation_id:
            values['account_depreciation_id'] = self.account_depreciation_id.id
        if self.account_depreciation_expense_id != asset.account_depreciation_expense_id:
            values['account_depreciation_expense_id'] = self.account_depreciation_expense_id.id
        if (float_compare(self.method_progress_factor,
                          asset.method_progress_factor,
                          precision_digits=2) != 0):
            values['method_progress_factor'] = self.method_progress_factor
        if (float_compare(self.salvage_value, asset.salvage_value,
                          precision_rounding=asset.currency_id.rounding) != 0):
            values['salvage_value'] = self.salvage_value
        if (float_compare(self.value_residual, asset.value_residual,
                          precision_rounding=asset.currency_id.rounding) != 0):
            new_remaining_value = self.value_residual
            increase = new_remaining_value - asset.value_residual
            new_salvage = asset.salvage_value
            asset_original_value = asset.original_value + increase
            values.update({
                'original_value': asset_original_value,
                'salvage_value': new_salvage,
            })
            if not float_is_zero(
                    increase,
                    precision_rounding=asset.currency_id.rounding):
                asset.message_post(body=_(
                    "Value re-evaluated by %(amount)s. %(reason)s",
                    amount=increase, reason=self.name))
        if values:
            asset.write(values)
            asset.compute_depreciation_board()
            if self.name:
                asset.message_post(body=_(
                    "Asset modified: %s", self.name))

    def _pause_asset(self):
        self.ensure_one()
        return self.asset_id.pause(self.date, message=self.name)

    def _resume_asset(self):
        self.ensure_one()
        asset = self.asset_id
        pause_date = asset.disposal_date or self.date
        resume_date = self.date
        if resume_date and pause_date:
            days_between = (resume_date - pause_date).days
            asset.asset_paused_days += days_between
        
        values = {'state': 'open'}
        if self.method_number != asset.method_number:
            values['method_number'] = self.method_number
        if self.method_period != asset.method_period:
            values['method_period'] = self.method_period
        if self.method != asset.method:
            values['method'] = self.method
        if (float_compare(self.method_progress_factor,
                          asset.method_progress_factor,
                          precision_digits=2) != 0):
            values['method_progress_factor'] = self.method_progress_factor
        if (float_compare(self.salvage_value, asset.salvage_value,
                          precision_rounding=asset.currency_id.rounding) != 0):
            values['salvage_value'] = self.salvage_value
            
        if float_compare(self.value_residual, asset.value_residual, precision_rounding=asset.currency_id.rounding) != 0:
            new_remaining_value = self.value_residual
            increase = new_remaining_value - asset.value_residual
            new_salvage = self.salvage_value
            asset_original_value = asset.original_value + increase
            values.update({
                'original_value': asset_original_value,
                'salvage_value': new_salvage,
            })
            if not float_is_zero(increase, precision_rounding=asset.currency_id.rounding):
                asset.message_post(body=_(
                    "Value re-evaluated by %(amount)s upon resume.",
                    amount=increase))
                    
        asset.write(values)
        asset.compute_depreciation_board()
        asset.message_post(body=_(
            "Asset resumed after pause. %s",
            self.name if self.name else ""))
