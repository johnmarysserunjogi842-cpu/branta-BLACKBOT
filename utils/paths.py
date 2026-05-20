import sys
import os

def get_base_path():
    if getattr(sys, 'frozen', False):
        return sys._MEIPASS  # PyInstaller temp folder
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
