import os
import sys

# Add odoo path to sys.path
odoo_path = '/home/digitz/odoo16'
sys.path.append(odoo_path)

import odoo
from odoo.modules.registry import Registry

def check_module_status():
    # Load configuration
    config_file = '/home/digitz/odoo16/odoo.conf'
    odoo.tools.config.parse_config(['-c', config_file])
    
    db_name = odoo.tools.config['db_name']
    if not db_name:
        # Try to find db_name from env if not in config
        db_name = 'odoo16' # Fallback to common name if possible
    
    print(f"Checking module status in database: {db_name}")
    
    try:
        registry = Registry(db_name)
        with registry.cursor() as cr:
            env = odoo.api.Environment(cr, odoo.SUPERUSER_ID, {})
            
            # Check module state
            module = env['ir.module.module'].search([('name', '=', 'advanced_asset_management_pro')])
            if module:
                print(f"Module 'advanced_asset_management_pro' state: {module.state}")
            else:
                print("Module 'advanced_asset_management_pro' NOT FOUND in ir.module.module")
            
            # Check if account.asset is in registry
            if 'account.asset' in env.registry:
                print("Model 'account.asset' IS in registry.")
            else:
                print("Model 'account.asset' IS NOT in registry.")
                
            # Check for any other models starting with account.asset
            asset_models = [m for m in env.registry if m.startswith('account.asset')]
            print(f"Other asset models in registry: {asset_models}")

    except Exception as e:
        print(f"Error connecting to database: {e}")

if __name__ == '__main__':
    check_module_status()
