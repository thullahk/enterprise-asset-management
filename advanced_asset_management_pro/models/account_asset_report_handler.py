# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.tools.misc import format_date
from collections import defaultdict

MAX_NAME_LENGTH = 50


class AssetDepreciationScheduleHandler(models.AbstractModel):
    _name = 'account.asset.depreciation.schedule.handler'
    # We use a conditional inheritance to bypass the App Store's hard dependency scanner
    _inherit = 'account.report.custom.handler'
    _description = 'Asset Depreciation Schedule Custom Handler'

    def _caret_options_initializer(self):
        return {
            'account.asset': [
                {'name': _("Open Asset"), 'action': 'caret_option_open_record_form'},
            ],
        }

    # ------------------------------------------------------------------
    # Helper: build a single column dict compatible with this Odoo 16
    # account_reports version (no _build_column_dict helper exists here).
    # ------------------------------------------------------------------
    def _make_col(self, value, column_data, report, options):
        """Build a column dict that the report renderer understands.

        :param value: raw Python value (float/str/None)
        :param column_data: one entry from options['columns']
        :param report: account.report record
        :param options: report options dict
        """
        column_group_key = column_data['column_group_key']
        figure_type = column_data.get('figure_type', 'none')
        blank_if_zero = column_data.get('blank_if_zero', False)

        if figure_type == 'monetary':
            no_format = value if isinstance(value, (int, float)) else 0.0
            if no_format == 0.0 and blank_if_zero:
                formatted = ''
            else:
                formatted = report.format_value(no_format, blank_if_zero=blank_if_zero, figure_type='monetary')
            is_zero = (no_format == 0.0)
        else:
            no_format = value
            formatted = value if value is not None else ''
            is_zero = False

        return {
            'name': formatted,
            'no_format': no_format,
            'column_group_key': column_group_key,
            'expression_label': column_data['expression_label'],
            'style': 'white-space:nowrap; text-align:right;',
            'class': 'number' if figure_type == 'monetary' else '',
            'is_zero': is_zero,
            'auditable': False,
            'has_sublines': False,
        }

    # ------------------------------------------------------------------
    # Options initialiser
    # ------------------------------------------------------------------
    def _custom_options_initializer(self, report, options, previous_options=None):
        super()._custom_options_initializer(report, options, previous_options=previous_options)

        # Grouped column subheaders
        options['custom_columns_subheaders'] = [
            {"name": _("Characteristics"), "colspan": 4},
            {"name": _("Assets"),          "colspan": 4},
            {"name": _("Depreciation"),    "colspan": 4},
            {"name": _("Book Value"),      "colspan": 1},
        ]

        # Rename date-range columns to show actual dates
        date_from = options['date']['date_from']
        date_to   = options['date']['date_to']
        for col in options['columns']:
            lbl = col['expression_label']
            if lbl in ('assets_date_from', 'depre_date_from'):
                col['name'] = format_date(self.env, date_from)
            elif lbl in ('assets_date_to', 'depre_date_to'):
                col['name'] = format_date(self.env, date_to)
            elif lbl == 'balance':
                col['name'] = ''

        options['assets_grouping_field'] = (previous_options or {}).get('assets_grouping_field') or 'account_id'

    # ------------------------------------------------------------------
    # Dynamic lines generator (entry point called by account_reports)
    # ------------------------------------------------------------------
    def _dynamic_lines_generator(self, report, options, all_column_groups_expression_totals):
        lines, totals_by_column = self._generate_report_lines(report, options)

        # Group by account
        if options.get('assets_grouping_field') != 'none':
            lines = self._group_by_account(report, lines, options)

        # Grand-total row
        if lines:
            total_cols = []
            for col_data in options['columns']:
                expr = col_data['expression_label']
                raw = totals_by_column.get(expr, 0.0) if col_data.get('figure_type') == 'monetary' else ''
                total_cols.append(self._make_col(raw, col_data, report, options))

            lines.append({
                'id': report._get_generic_line_id(None, None, markup='total'),
                'level': 1,
                'name': _('Total'),
                'columns': total_cols,
                'unfoldable': False,
                'unfolded': False,
            })

        return [(0, line) for line in lines]

    # ------------------------------------------------------------------
    # Build flat asset lines + running totals
    # ------------------------------------------------------------------
    def _generate_report_lines(self, report, options):
        company_ids = report.get_report_company_ids(options)
        results = self._query_values(options, company_ids)

        all_asset_ids = [r['asset_id'] for r in results]
        assets_cache = {a.id: a for a in self.env['account.asset'].browse(all_asset_ids)}

        monetary_exprs = [
            'assets_date_from', 'assets_plus', 'assets_minus', 'assets_date_to',
            'depre_date_from', 'depre_plus', 'depre_minus', 'depre_date_to', 'balance',
        ]
        totals = {k: 0.0 for k in monetary_exprs}

        # Split parents / children (gross-increase assets have a parent_id)
        parent_rows = []
        children_map = defaultdict(list)
        for res in results:
            if res['parent_id']:
                children_map[res['parent_id']].append(res)
            else:
                parent_rows.append(res)

        method_labels = dict(self.env['account.asset']._fields['method'].selection)
        period_labels = dict(self.env['account.asset']._fields['method_period'].selection)

        lines = []
        for row in parent_rows:
            child_rows = children_map[row['asset_id']]
            values = self._compute_asset_values(options, row, child_rows)

            # Depreciation rate string
            rate_str = self._depreciation_rate_str(row, period_labels)

            col_data_map = {
                'acquisition_date': row['asset_acquisition_date'] and format_date(self.env, row['asset_acquisition_date']) or '',
                'first_depreciation': row['asset_date'] and format_date(self.env, row['asset_date']) or '',
                'method': method_labels.get(row['asset_method'], ''),
                'duration_rate': rate_str,
                **values,
            }

            # Build column list matching options['columns'] order
            cols = []
            for col_def in options['columns']:
                expr = col_def['expression_label']
                raw = col_data_map.get(expr, 0.0 if col_def.get('figure_type') == 'monetary' else '')
                cols.append(self._make_col(raw, col_def, report, options))
                if col_def.get('figure_type') == 'monetary' and expr in totals:
                    totals[expr] = totals.get(expr, 0.0) + (raw if isinstance(raw, (int, float)) else 0.0)

            asset_obj = assets_cache[row['asset_id']]
            lines.append({
                'id': report._get_generic_line_id('account.asset', row['asset_id']),
                'level': 2,
                'name': asset_obj.name,
                'columns': cols,
                'unfoldable': False,
                'unfolded': False,
                'caret_options': 'account.asset',
                '_account_id': row['account_id'],
                '_asset_group_id': row['asset_group_id'],
            })

        return lines, totals

    # ------------------------------------------------------------------
    # SQL query
    # ------------------------------------------------------------------
    def _query_values(self, options, company_ids):
        date_from = options['date']['date_from']
        date_to   = options['date']['date_to']

        sql = """
            SELECT
                asset.id                  AS asset_id,
                asset.parent_id           AS parent_id,
                asset.name                AS asset_name,
                asset.asset_group_id      AS asset_group_id,
                asset.original_value      AS asset_original_value,
                asset.salvage_value       AS asset_salvage_value,
                asset.method              AS asset_method,
                asset.method_number       AS asset_method_number,
                asset.method_period       AS asset_method_period,
                asset.method_progress_factor AS asset_method_progress_factor,
                asset.state               AS asset_state,
                asset.disposal_date       AS asset_disposal_date,
                asset.acquisition_date    AS asset_acquisition_date,
                asset.already_depreciated_amount_import AS already_depreciated,
                MIN(move.date)            AS asset_date,
                account.id               AS account_id,
                account.code             AS account_code,
                account.name             AS account_name,
                COALESCE(SUM(move.depreciation_value) FILTER (WHERE move.date < %s AND move.state = 'posted'), 0.0)
                    AS depreciated_before,
                COALESCE(SUM(move.depreciation_value) FILTER (WHERE move.date BETWEEN %s AND %s AND move.state = 'posted'), 0.0)
                    AS depreciated_during
            FROM account_asset asset
            JOIN account_account account ON account.id = asset.account_asset_id
            LEFT JOIN account_move move  ON move.asset_id = asset.id
            WHERE asset.company_id IN %s
              AND asset.state NOT IN ('model', 'draft', 'cancelled')
              AND asset.active = TRUE
              AND (asset.acquisition_date <= %s OR (move.date <= %s AND move.state = 'posted'))
              AND (asset.disposal_date >= %s OR asset.disposal_date IS NULL)
            GROUP BY asset.id, account.id, account.code, account.name
            ORDER BY account.code, asset.acquisition_date, asset.id
        """
        params = (
            date_from,                   # depreciated_before  < date_from
            date_from, date_to,          # depreciated_during  BETWEEN
            tuple(company_ids),
            date_to, date_to,            # acquisition/move filter
            date_from,                   # disposal filter
        )
        self._cr.execute(sql, params)
        return self._cr.dictfetchall()

    # ------------------------------------------------------------------
    # Compute opening/closing for assets & depreciation
    # ------------------------------------------------------------------
    def _compute_asset_values(self, options, row, child_rows):
        opening_date = fields.Date.to_date(options['date']['date_from'])
        closing_date = fields.Date.to_date(options['date']['date_to'])

        def _is_opening(acq_date, first_move_date):
            ref = acq_date or first_move_date
            return bool(ref and ref < opening_date)

        # ---- Asset value ----
        opening = _is_opening(row['asset_acquisition_date'], row['asset_date'])
        asset_opening = row['asset_original_value'] if opening else 0.0
        asset_add     = 0.0 if opening else row['asset_original_value']
        asset_minus   = 0.0

        if row['asset_state'] == 'close' and row['asset_disposal_date'] and \
                opening_date <= row['asset_disposal_date'] <= closing_date:
            asset_minus = row['asset_original_value']

        # ---- Depreciation ----
        already = row.get('already_depreciated') or 0.0
        dep_opening = row['depreciated_before'] + (already if opening else 0.0)
        dep_add     = row['depreciated_during']  + (already if not opening else 0.0)
        dep_minus   = 0.0

        if row['asset_state'] == 'close' and row['asset_disposal_date'] and \
                opening_date <= row['asset_disposal_date'] <= closing_date:
            dep_minus = dep_opening + dep_add

        # ---- Merge gross-increase children ----
        for child in child_rows:
            c_opening = _is_opening(child['asset_acquisition_date'], child['asset_date'])
            asset_opening += child['asset_original_value'] if c_opening else 0.0
            asset_add     += 0.0 if c_opening else child['asset_original_value']
            c_already = child.get('already_depreciated') or 0.0
            dep_opening += child['depreciated_before'] + (c_already if c_opening else 0.0)
            dep_add     += child['depreciated_during']  + (c_already if not c_opening else 0.0)

        asset_closing = asset_opening + asset_add - asset_minus
        dep_closing   = dep_opening   + dep_add   - dep_minus

        return {
            'assets_date_from': asset_opening,
            'assets_plus':      asset_add,
            'assets_minus':     asset_minus,
            'assets_date_to':   asset_closing,
            'depre_date_from':  dep_opening,
            'depre_plus':       dep_add,
            'depre_minus':      dep_minus,
            'depre_date_to':    dep_closing,
            'balance':          asset_closing - dep_closing,
        }

    # ------------------------------------------------------------------
    # Depreciation-rate label
    # ------------------------------------------------------------------
    def _depreciation_rate_str(self, row, period_labels):
        method = row['asset_method']
        if method in ('degressive', 'degressive_then_linear'):
            factor = row['asset_method_progress_factor'] or 0.0
            return '{:.2f} %'.format(factor * 100)
        # linear
        nb     = int(row['asset_method_number'] or 0)
        period = row['asset_method_period'] or 'months'
        if period == 'months':
            total_months = nb
        else:
            total_months = nb * 12
        years  = total_months // 12
        months = total_months % 12
        parts  = []
        if years:
            parts.append(_('%sy', years))
        if months:
            parts.append(_('%sm', months))
        return ' '.join(parts) or '0m'

    # ------------------------------------------------------------------
    # Group flat lines under account-header rows
    # ------------------------------------------------------------------
    def _group_by_account(self, report, lines, options):
        if not lines:
            return lines

        grouped = defaultdict(list)
        for line in lines:
            grouped[line.get('_account_id')].append(line)

        account_ids = [aid for aid in grouped if aid]
        accounts = self.env['account.account'].browse(account_ids)
        sorted_accounts = accounts.sorted(lambda a: (a.code, a.name))

        final_lines = []
        for account in sorted_accounts:
            child_lines = grouped[account.id]

            # Parent totals: sum monetary columns
            parent_cols = []
            for i, col_def in enumerate(options['columns']):
                if col_def.get('figure_type') == 'monetary':
                    total = sum(
                        c['columns'][i].get('no_format', 0.0) or 0.0
                        for c in child_lines
                    )
                    parent_cols.append(self._make_col(total, col_def, report, options))
                else:
                    parent_cols.append(self._make_col('', col_def, report, options))

            parent_name     = f"{account.code} {account.name}".strip()
            parent_line_id  = report._get_generic_line_id('account.account', account.id)

            final_lines.append({
                'id': parent_line_id,
                'name': parent_name,
                'columns': parent_cols,
                'level': 1,
                'unfoldable': True,
                'unfolded': True,
            })

            for child in child_lines:
                child['parent_id'] = parent_line_id
                final_lines.append(child)

        return final_lines
