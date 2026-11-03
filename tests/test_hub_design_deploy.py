"""Local design install and rollback use exact running build verification."""
from tests import test_update_status_ui_deploy as deploy_tests


class HubDesignDeployTests(deploy_tests.UpdateStatusDeployTests):
    deploy_file = 'deploy-hub-design.ps1'
    backup_name = 'hub.py.bak-before-hub-design-v120'
    success_marker = 'HUB_DESIGN_V120_Install_OK'
