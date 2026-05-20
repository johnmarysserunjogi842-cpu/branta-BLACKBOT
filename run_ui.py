import sys
import os

# ✅ Ensure root directory is in path
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(BASE_DIR)

# ✅ Safe imports (use full paths)
from UI.controller import Controller
from UI.main_window import MainWindow
from UI.dashboard import Dashboard
from UI.sidebar import Sidebar
from connectors.app_connector import AppConnector

DEBUG = True

if DEBUG:
    print("Starting UI...")
def run():
    try:
        # 🔌 Initialize connector (replace None later with real components)
        app_connector = AppConnector(
            market_cache=None,
            metrics=None,
            feedback=None,
            account=None,
        )

        controller = Controller(app_connector)

        # 🪟 Main UI
        window = MainWindow(controller)

        dashboard = Dashboard(window.main_frame, controller)
        window.register_view("dashboard", dashboard)

        sidebar = Sidebar(
            window.sidebar_frame,
            controller,
            dashboard.switch_view
        )
        window.attach_sidebar(sidebar)

        window.switch_view("dashboard")
        window.run()

    except Exception as e:
        print("❌ Application crashed:")
        print(e)
        input("Press Enter to exit...")  # Keeps .exe open


if __name__ == "__main__":
    run()
