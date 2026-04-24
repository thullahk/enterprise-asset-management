# -*- coding: utf-8 -*-
from odoo import api, fields, models
from odoo.tools.misc import format_date


class DepreciationScheduleWizard(models.TransientModel):
    _name = 'asset.depreciation.schedule.wizard'
    _description = 'Depreciation Schedule Report Wizard'

    date_from = fields.Date(
        string='Start Date',
        required=True,
        default=lambda self: fields.Date.today().replace(month=1, day=1),
    )
    date_to = fields.Date(
        string='End Date',
        required=True,
        default=lambda self: fields.Date.today().replace(month=12, day=31),
    )
    company_id = fields.Many2one(
        'res.company',
        string='Company',
        required=True,
        default=lambda self: self.env.company,
    )

    def action_print_pdf(self):
        data = self._get_report_data()
        return self.env.ref('advanced_asset_management_pro.action_report_depreciation_schedule').report_action(
            self, data=data
        )

    def action_print_xlsx(self):
        data = self._get_report_data()
        return self.env.ref('advanced_asset_management_pro.action_report_depreciation_schedule').report_action(
            self, data=data, config=False
        )

    def _get_report_data(self):
        date_from = self.date_from
        date_to = self.date_to
        company = self.company_id
        currency = company.currency_id

        # Fetch all non-model/non-cancelled assets for the company
        assets = self.env['account.asset'].search([
            ('state', 'not in', ['draft', 'model', 'cancelled']),
            ('company_id', '=', company.id),
        ])

        # Group by account_asset_id
        grouped = {}
        for asset in assets:
            acc = asset.account_asset_id
            if not acc:
                continue
            key = (acc.id, f"{acc.code} {acc.name}")
            if key not in grouped:
                grouped[key] = []
            grouped[key].append(asset)

        lines = []
        totals = {
            'asset_opening': 0.0, 'asset_add': 0.0,
            'asset_minus': 0.0, 'asset_closing': 0.0,
            'dep_opening': 0.0, 'dep_add': 0.0,
            'dep_minus': 0.0, 'dep_closing': 0.0,
            'book_value': 0.0,
        }

        method_selection = dict(
            self.env['account.asset']._fields['method'].selection
        )
        period_selection = dict(
            self.env['account.asset']._fields['method_period'].selection
        )

        for (acc_id, acc_name), acc_assets in sorted(grouped.items(), key=lambda x: x[0][1]):
            group_line = {
                'is_group': True,
                'name': acc_name,
                'asset_opening': 0.0, 'asset_add': 0.0,
                'asset_minus': 0.0, 'asset_closing': 0.0,
                'dep_opening': 0.0, 'dep_add': 0.0,
                'dep_minus': 0.0, 'dep_closing': 0.0,
                'book_value': 0.0,
                'children': [],
            }

            for asset in acc_assets:
                acq_date = asset.acquisition_date or asset.prorata_date

                # ---- Asset Value bounds ----
                if acq_date and acq_date < date_from:
                    asset_opening = asset.original_value
                    asset_add = 0.0
                else:
                    asset_opening = 0.0
                    asset_add = asset.original_value if (acq_date and date_from <= acq_date <= date_to) else 0.0

                asset_minus = 0.0
                if asset.state == 'close' and asset.disposal_date:
                    if date_from <= asset.disposal_date <= date_to:
                        asset_minus = asset.original_value

                asset_closing = asset_opening + asset_add - asset_minus

                # ---- Depreciation bounds ----
                dep_moves = asset.depreciation_move_ids.filtered(
                    lambda m: m.state == 'posted' and m.asset_depreciation_beginning_date is not False
                )

                dep_opening = sum(m.depreciation_value for m in dep_moves if m.date < date_from)
                if acq_date and acq_date < date_from:
                    dep_opening += asset.already_depreciated_amount_import

                dep_add = sum(m.depreciation_value for m in dep_moves
                              if date_from <= m.date <= date_to)
                if acq_date and date_from <= acq_date <= date_to:
                    dep_add += asset.already_depreciated_amount_import

                dep_minus = 0.0
                if asset.state == 'close' and asset.disposal_date:
                    if date_from <= asset.disposal_date <= date_to:
                        dep_minus = dep_opening + dep_add

                dep_closing = dep_opening + dep_add - dep_minus
                book_value = asset_closing - dep_closing

                # Method / duration text
                method_str = method_selection.get(asset.method, asset.method)
                period_str = period_selection.get(asset.method_period, asset.method_period)
                if asset.method == 'degressive':
                    duration_str = f"{asset.method_progress_factor * 100:.0f}%"
                else:
                    duration_str = f"{asset.method_number} {period_str}"

                child = {
                    'is_group': False,
                    'name': asset.name,
                    'acquisition_date': format_date(self.env, asset.acquisition_date) if asset.acquisition_date else '',
                    'first_depreciation': format_date(self.env, asset.prorata_date) if asset.prorata_date else '',
                    'method': method_str,
                    'duration_rate': duration_str,
                    'asset_opening': currency.round(asset_opening),
                    'asset_add': currency.round(asset_add),
                    'asset_minus': currency.round(asset_minus),
                    'asset_closing': currency.round(asset_closing),
                    'dep_opening': currency.round(dep_opening),
                    'dep_add': currency.round(dep_add),
                    'dep_minus': currency.round(dep_minus),
                    'dep_closing': currency.round(dep_closing),
                    'book_value': currency.round(book_value),
                }
                group_line['children'].append(child)

                # Accumulate group totals
                group_line['asset_opening'] += asset_opening
                group_line['asset_add'] += asset_add
                group_line['asset_minus'] += asset_minus
                group_line['asset_closing'] += asset_closing
                group_line['dep_opening'] += dep_opening
                group_line['dep_add'] += dep_add
                group_line['dep_minus'] += dep_minus
                group_line['dep_closing'] += dep_closing
                group_line['book_value'] += book_value

            # Round group totals
            for k in ('asset_opening', 'asset_add', 'asset_minus', 'asset_closing',
                      'dep_opening', 'dep_add', 'dep_minus', 'dep_closing', 'book_value'):
                group_line[k] = currency.round(group_line[k])
                totals[k] += group_line[k]

            lines.append(group_line)

        return {
            'date_from': format_date(self.env, date_from),
            'date_to': format_date(self.env, date_to),
            'company_name': company.name,
            'currency_symbol': currency.symbol,
            'currency_position': currency.position,
            'lines': lines,
            'totals': totals,
        }
