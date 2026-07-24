"""
yolo CLI wrapper — replaces broken yolo.exe shim after project move.
Usage: python yolo_cli.py train model=... data=... ...
"""
import sys
from ultralytics.cfg import entrypoint

if __name__ == '__main__':
    entrypoint()
